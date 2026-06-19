"""Cliente HTTP para Koha (DigiBePé) vía sesión de staff + ejecutar/descargar informes.

Koha 3.x no tiene API REST. Tras experimentar contra el servidor real descubrimos que:
  - `svc/report` (JSON) está LIMITADO a 10 filas y NO aplica parámetros → inservible.
  - La vía que funciona es la de la interfaz: "Ejecutar informe" + "Descargar":
      1. GET guided_reports.pl?reports=ID&phase=Run this report[&sql_params=valor...]
         (esto SÍ aplica los parámetros <<...>> del informe).
      2. La página de resultados trae un formulario de descarga con el SQL ya
         resuelto. Se hace POST con phase=Export & format=tab → devuelve TODAS las
         filas como texto separado por tabuladores (con encabezado de columnas).

Así obtenemos datos completos y filtrados. El encabezado del export da los nombres
de columna, así que devolvemos directamente filas como diccionarios.
"""
from __future__ import annotations

import asyncio
import csv
import io
import logging
import re

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger("koha.client")

LOGIN_PATH = "/cgi-bin/koha/mainpage.pl"
RUN_PATH = "/cgi-bin/koha/reports/guided_reports.pl"
LOGIN_MARKER = "auth.tt"


class KohaError(Exception):
    """Error genérico al hablar con Koha."""


class KohaAuthError(KohaError):
    """Falló el login o la sesión no es válida."""


class KohaClient:
    """Mantiene una sesión autenticada contra Koha y ejecuta/descarga informes."""

    def __init__(self, base_url: str, userid: str, password: str, timeout: float = 90.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._userid = userid
        self._password = password
        self._client = httpx.AsyncClient(
            base_url=self._base_url, timeout=timeout, follow_redirects=True
        )
        self._logged_in = False
        self._lock = asyncio.Lock()
        self._sql_cache: dict[int, str] = {}  # report_id -> SQL guardado (se busca una vez)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def login(self) -> None:
        """Inicia sesión en la intranet. Lanza KohaAuthError si falla."""
        if not self._userid or not self._password:
            raise KohaAuthError("Faltan credenciales de Koha.")
        resp = await self._client.post(
            LOGIN_PATH,
            data={"userid": self._userid, "password": self._password,
                  "koha_login_context": "intranet"},
        )
        if LOGIN_MARKER in resp.text:
            self._logged_in = False
            raise KohaAuthError("Login rechazado por Koha (revisar usuario/contraseña/permisos).")
        self._logged_in = True
        logger.info("Sesión Koha iniciada como %s", self._userid)

    async def _ensure_login(self) -> None:
        async with self._lock:
            if not self._logged_in:
                await self.login()

    async def _get_report_sql(self, report_id: int) -> str:
        """Devuelve el SQL guardado del informe (se busca una sola vez y se cachea)."""
        if report_id in self._sql_cache:
            return self._sql_cache[report_id]
        resp = await self._client.get(RUN_PATH, params={"reports": report_id, "phase": "Edit SQL"})
        if LOGIN_MARKER in resp.text:
            await self.login()
            resp = await self._client.get(RUN_PATH, params={"reports": report_id, "phase": "Edit SQL"})
        soup = BeautifulSoup(resp.text, "html.parser")
        ta = soup.find("textarea", attrs={"name": "sql"})
        if ta is None:
            raise KohaError(f"No pude leer el SQL del informe {report_id}.")
        sql = (ta.text or "").strip()
        self._sql_cache[report_id] = sql
        return sql

    @staticmethod
    def _substitute(sql: str, params: list[str]) -> str:
        """Reemplaza los placeholders <<...>> por los valores, EN ORDEN, escapando comillas.

        Koha normalmente hace esto al ejecutar; lo replicamos para ir directo al export
        en un solo request. Los valores se citan como string (sirve para LIKE y = sobre
        cardnumber/textos) y se escapan las comillas simples para evitar romper el SQL.
        """
        values = iter(params)

        def repl(_m):
            try:
                v = next(values)
            except StopIteration:
                return _m.group(0)
            return "'" + str(v).replace("'", "''") + "'"

        return re.sub(r"<<[^>]*>>", repl, sql)

    async def run_report(self, report_id: int, params: list[str] | None = None) -> list[dict]:
        """Ejecuta un informe guardado y devuelve TODAS sus filas como dicts.

        Optimizado: arma el SQL con los parámetros y va DIRECTO al export (1 request),
        en vez de ejecutar+descargar (2 requests). El SQL del informe se cachea por id.

        params: valores para los placeholders <<...>> del informe, EN ORDEN.
        """
        await self._ensure_login()
        sql = await self._get_report_sql(report_id)
        final_sql = self._substitute(sql, params or [])

        data = {"sql": final_sql, "format": "tab", "phase": "Export", "submit": "Bajar"}
        resp = await self._client.post(RUN_PATH, data=data)
        if LOGIN_MARKER in resp.text:
            async with self._lock:
                await self.login()
            resp = await self._client.post(RUN_PATH, data=data)

        # Si Koha devolvió HTML de error en vez del TSV, avisamos.
        head = resp.text[:4000]
        if "\t" not in head and "dialog alert" in head:
            soup = BeautifulSoup(resp.text, "html.parser")
            alert = soup.find(class_="dialog alert")
            detail = alert.get_text(" ", strip=True) if alert else "error desconocido"
            raise KohaError(f"Informe {report_id}: {detail}")

        return self._parse_tsv(resp.text)

    @staticmethod
    def _parse_tsv(text: str) -> list[dict]:
        """Parsea el export separado por tabuladores en lista de dicts (clave = encabezado)."""
        reader = csv.reader(io.StringIO(text), delimiter="\t")
        rows = [r for r in reader if r and any(c.strip() for c in r)]
        if not rows:
            return []
        header = [h.strip() for h in rows[0]]
        out: list[dict] = []
        for r in rows[1:]:
            out.append({header[i]: (r[i] if i < len(r) else None) for i in range(len(header))})
        return out
