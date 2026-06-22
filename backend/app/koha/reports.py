"""Capa de datos: traduce operaciones de negocio en reportes reales de Koha.

Cada operación (buscar socios, préstamos vencidos, etc.) corresponde a un reporte
SQL guardado en la intranet de Koha (ver ../sql/). El SQL queda escondido acá; la
API y el frontend solo ven métodos limpios que devuelven listas de diccionarios.

Los datos son SIEMPRE reales: se obtienen ejecutando los reportes vía la SESIÓN de
la bibliotecaria autenticada (ver auth.py), de modo que Koha aplica sus permisos.

IMPORTANTE sobre el mapeo de columnas:
  svc/report devuelve las filas en el ORDEN del SELECT. Por eso cada ReportSpec
  declara `columns` en el MISMO orden que el SELECT del .sql correspondiente.
  `_map_rows` soporta filas como listas (orden) o como objetos (dict); el formato
  exacto de tu Koha se confirma con scripts/probe_koha.py.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ..config import settings
from .client import KohaClient, KohaError

logger = logging.getLogger("koha.reports")


@dataclass(frozen=True)
class ReportSpec:
    """Describe un reporte de Koha: qué id usar y cómo nombrar sus columnas."""

    key: str
    id_setting: str          # atributo en settings con el id (ej "report_member_search_id")
    columns: list[str]       # nombres de columnas EN EL ORDEN del SELECT
    param_count: int = 0     # cuántos placeholders <<...>> espera

    def report_id(self) -> int:
        value = getattr(settings, self.id_setting, None)
        if not value:
            raise KohaError(
                f"El reporte '{self.key}' no tiene id configurado "
                f"({self.id_setting.upper()} vacío en .env). Creá el reporte en Koha y cargá su id."
            )
        return int(value)


# Registro de reportes. `columns` debe coincidir con el SELECT de cada archivo en sql/.
REPORTS: dict[str, ReportSpec] = {
    "member_search": ReportSpec(
        key="member_search",
        id_setting="report_member_search_id",
        columns=["cardnumber", "surname", "firstname", "email", "phone", "category", "dateexpiry"],
        param_count=1,  # término de búsqueda (apellido/nombre/cardnumber)
    ),
    "member_loans": ReportSpec(
        key="member_loans",
        id_setting="report_member_loans_id",
        columns=["barcode", "title", "author", "issuedate", "date_due"],
        param_count=1,  # cardnumber
    ),
    "loans_active": ReportSpec(
        key="loans_active",
        id_setting="report_loans_active_id",
        columns=["cardnumber", "surname", "firstname", "barcode", "title", "issuedate", "date_due"],
        param_count=0,
    ),
    "loans_overdue": ReportSpec(
        key="loans_overdue",
        id_setting="report_loans_overdue_id",
        columns=["cardnumber", "surname", "firstname", "phone", "email", "barcode", "title", "date_due", "dias_atraso"],
        param_count=0,
    ),
    "member_profile": ReportSpec(
        key="member_profile",
        id_setting="report_member_profile_id",
        columns=["cardnumber", "surname", "firstname", "email", "phone", "mobile",
                 "address", "city", "category", "dateenrolled", "dateexpiry", "debarred", "deuda"],
        param_count=1,
    ),
    "member_account": ReportSpec(
        key="member_account",
        id_setting="report_member_account_id",
        columns=["date", "accounttype", "description", "amount", "amountoutstanding"],
        param_count=1,
    ),
    "member_history": ReportSpec(
        key="member_history",
        id_setting="report_member_history_id",
        columns=["barcode", "title", "author", "issuedate", "returndate"],
        param_count=1,
    ),
    "loans_contact": ReportSpec(
        key="loans_contact",
        id_setting="report_loans_contact_id",
        columns=["cardnumber", "surname", "firstname", "email", "phone",
                 "barcode", "title", "issuedate", "date_due", "dias_atraso"],
        param_count=0,
    ),
}


def _map_rows(spec: ReportSpec, raw: Any) -> list[dict[str, Any]]:
    """Convierte la respuesta cruda de svc/report en lista de dicts con nombres."""
    if not isinstance(raw, list):
        raise KohaError(f"Respuesta inesperada de svc/report para '{spec.key}': {type(raw)}")
    out: list[dict[str, Any]] = []
    for row in raw:
        if isinstance(row, dict):
            out.append(row)
        elif isinstance(row, (list, tuple)):
            out.append(dict(zip(spec.columns, row)))
        else:
            raise KohaError(f"Fila inesperada en '{spec.key}': {row!r}")
    return out


class KohaRepository:
    """Acceso a datos reales para la sesión de UNA bibliotecaria autenticada."""

    def __init__(self, client: KohaClient) -> None:
        self._client = client

    async def _run(self, spec: ReportSpec, params: list[str] | None = None) -> list[dict[str, Any]]:
        raw = await self._client.run_report(spec.report_id(), params)
        return _map_rows(spec, raw)

    # ── Operaciones de negocio ────────────────────────────────────────────
    async def search_members(self, query: str) -> list[dict[str, Any]]:
        return await self._run(REPORTS["member_search"], [f"%{query}%"])

    async def member_loans(self, cardnumber: str) -> list[dict[str, Any]]:
        return await self._run(REPORTS["member_loans"], [cardnumber])

    async def active_loans(self) -> list[dict[str, Any]]:
        return await self._run(REPORTS["loans_active"])

    async def overdue_loans(self) -> list[dict[str, Any]]:
        return await self._run(REPORTS["loans_overdue"])

    async def member_profile(self, cardnumber: str) -> list[dict[str, Any]]:
        return await self._run(REPORTS["member_profile"], [cardnumber])

    async def member_account(self, cardnumber: str) -> list[dict[str, Any]]:
        return await self._run(REPORTS["member_account"], [cardnumber])

    async def member_history(self, cardnumber: str) -> list[dict[str, Any]]:
        return await self._run(REPORTS["member_history"], [cardnumber])

    async def loans_contact(self) -> list[dict[str, Any]]:
        """Todos los préstamos vigentes con contacto y días respecto del vencimiento."""
        return await self._run(REPORTS["loans_contact"])

    async def run_sql(self, sql: str) -> list[dict[str, Any]]:
        """Consulta SQL interna (estadísticas de catálogo). Sin entrada del usuario."""
        return await self._client.run_sql(sql)
