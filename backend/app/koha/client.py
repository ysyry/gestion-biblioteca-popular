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


def sql_literal(valor) -> str:
    """Devuelve `valor` como string SQL de MySQL, listo para pegar en una consulta.

    En MySQL la barra invertida también escapa dentro de un string. Duplicar solo las
    comillas no alcanza: con `\\'` la comilla "escapada" cierra el string y lo que
    sigue se ejecuta como SQL. Por eso primero se duplican las barras y después las
    comillas; el byte nulo va como `\\0`.
    """
    s = str(valor).replace("\\", "\\\\").replace("'", "''").replace("\x00", "\\0")
    return f"'{s}'"


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
        # Número de sesión. Sube en cada login. Sirve para que, si muchos pedidos
        # en paralelo se encuentran con la sesión vencida, se renueve UNA sola vez
        # y los demás reintenten con la cookie nueva (antes cada uno volvía a
        # loguear y se pisaban entre sí, devolviendo páginas HTML en vez de datos).
        self._gen = 0
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
        self._gen += 1
        logger.info("Sesión Koha iniciada como %s", self._userid)

    async def _ensure_login(self) -> None:
        async with self._lock:
            if not self._logged_in:
                await self.login()

    async def _renovar(self, gen: int) -> bool:
        """Renueva la sesión, pero solo si nadie más la renovó ya.

        `gen` es el número de sesión que tenía quien detectó el vencimiento. Si al
        tomar el lock la sesión ya es otra, alguien la renovó mientras esperábamos:
        no volvemos a loguear, solo avisamos que conviene reintentar.
        """
        async with self._lock:
            if gen != self._gen:
                return True          # ya la renovó otro: reintentá y listo
            await self.login()
            return True

    @staticmethod
    def _es_html(text: str) -> bool:
        """¿Koha devolvió una página web en vez del export de datos?

        El export es texto plano (TSV). Si empieza con HTML, algo salió mal:
        sesión vencida, sin permisos, o un error del servidor.
        """
        inicio = text.lstrip()[:200].lower()
        return inicio.startswith("<!doctype") or inicio.startswith("<html") or "<html" in inicio

    @classmethod
    def _exigir_datos(cls, text: str, contexto: str) -> None:
        """Corta con un error claro si la respuesta no son datos.

        Antes esto no se validaba: una página HTML se parseaba como si fuera TSV y
        salían filas basura que terminaban mostrándose como CEROS en los tableros.
        Un cero inventado es peor que un error, así que ahora falla a la vista.
        """
        if not cls._es_html(text):
            return
        soup = BeautifulSoup(text, "html.parser")
        alerta = soup.find(class_="dialog alert") or soup.find(class_="dialog message")
        if alerta:
            raise KohaError(f"{contexto}: {alerta.get_text(' ', strip=True)}")
        if LOGIN_MARKER in text or soup.find("input", attrs={"name": "koha_login_context"}):
            raise KohaAuthError(f"{contexto}: Koha pidió iniciar sesión de nuevo.")
        titulo = soup.find("title")
        detalle = titulo.get_text(strip=True) if titulo else "respuesta inesperada"
        raise KohaError(f"{contexto}: Koha devolvió una página web en vez de datos ({detalle}).")

    async def _get_report_sql(self, report_id: int) -> str:
        """Devuelve el SQL guardado del informe (se busca una sola vez y se cachea)."""
        if report_id in self._sql_cache:
            return self._sql_cache[report_id]
        params = {"reports": report_id, "phase": "Edit SQL"}
        gen = self._gen
        resp = await self._client.get(RUN_PATH, params=params)
        if LOGIN_MARKER in resp.text:
            await self._renovar(gen)
            resp = await self._client.get(RUN_PATH, params=params)
        soup = BeautifulSoup(resp.text, "html.parser")
        ta = soup.find("textarea", attrs={"name": "sql"})
        if ta is None:
            raise KohaError(f"No pude leer el SQL del informe {report_id}.")
        sql = (ta.text or "").strip()
        self._sql_cache[report_id] = sql
        return sql

    @staticmethod
    def _substitute(sql: str, params: list[str]) -> str:
        """Reemplaza los placeholders <<...>> por los valores, EN ORDEN, como strings SQL.

        Koha normalmente hace esto al ejecutar; lo replicamos para ir directo al export
        en un solo request. Los valores se citan como string (sirve para LIKE y = sobre
        cardnumber/textos) con `sql_literal`, que los escapa para MySQL.
        """
        values = iter(params)

        def repl(_m):
            try:
                v = next(values)
            except StopIteration:
                return _m.group(0)
            return sql_literal(v)

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
        gen = self._gen
        resp = await self._client.post(RUN_PATH, data=data)
        if self._es_html(resp.text):
            await self._renovar(gen)
            resp = await self._client.post(RUN_PATH, data=data)

        self._exigir_datos(resp.text, f"Informe {report_id}")
        return self._parse_tsv(resp.text)

    async def run_sql(self, sql: str) -> list[dict]:
        """Ejecuta un SELECT arbitrario (SQL interno de la app, sin entrada del usuario)
        contra el export de Koha y devuelve las filas. Para estadísticas de catálogo."""
        await self._ensure_login()
        data = {"sql": sql, "format": "tab", "phase": "Export", "submit": "Bajar"}
        gen = self._gen
        resp = await self._client.post(RUN_PATH, data=data)
        if self._es_html(resp.text):
            await self._renovar(gen)
            resp = await self._client.post(RUN_PATH, data=data)

        self._exigir_datos(resp.text, "Consulta de catálogo")
        return self._parse_tsv(resp.text)

    @staticmethod
    def _parse_tsv(text: str) -> list[dict]:
        """Parsea el export separado por tabuladores en lista de dicts (clave = encabezado)."""
        # Koha no cita ni escapa los campos: una comilla es parte del dato. Con el modo
        # por defecto, un título que empieza con comillas se "abría" y se tragaba las
        # columnas y filas siguientes hasta la próxima comilla.
        reader = csv.reader(io.StringIO(text), delimiter="\t", quoting=csv.QUOTE_NONE)
        rows = [r for r in reader if r and any(c.strip() for c in r)]
        if not rows:
            return []
        header = [h.strip() for h in rows[0]]
        out: list[dict] = []
        for r in rows[1:]:
            out.append({header[i]: (r[i] if i < len(r) else None) for i in range(len(header))})
        return out
