#!/usr/bin/env python3
"""Actualiza el SQL de reportes YA creados en Koha (preserva sus IDs).

Usa el formulario de edición (phase='Update SQL'). Útil cuando cambiás un .sql
(ej: emails con COALESCE) y querés reflejarlo sin recrear el reporte.

Uso:  python scripts/update_reports.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import settings  # noqa: E402

BASE = settings.koha_base_url.rstrip("/")
LOGIN = "/cgi-bin/koha/mainpage.pl"
REPORTS = "/cgi-bin/koha/reports/guided_reports.pl"
SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

# id_setting -> archivo .sql
TARGETS = {
    "report_member_search_id": "01_member_search.sql",
    "report_loans_overdue_id": "04_loans_overdue.sql",
    "report_member_profile_id": "05_member_profile.sql",
    "report_loans_contact_id": "08_loans_contact.sql",
}


def clean_sql(text: str) -> str:
    lines = text.splitlines()
    i = 0
    while i < len(lines) and (not lines[i].strip() or lines[i].lstrip().startswith("--")):
        i += 1
    sql = "\n".join(lines[i:]).strip()
    return sql[:-1].strip() if sql.endswith(";") else sql


def main() -> int:
    s = requests.Session()
    r = s.post(BASE + LOGIN, data={"userid": settings.koha_user, "password": settings.koha_password,
                                   "koha_login_context": "intranet"}, timeout=30)
    if "auth.tt" in r.text:
        print("✗ Login rechazado."); return 2
    print(f"✓ Login OK como {settings.koha_user}")

    for id_setting, fname in TARGETS.items():
        rid = getattr(settings, id_setting, None)
        if not rid:
            print(f"= {id_setting} sin id, salteo"); continue
        # 1) leer el formulario de edición para tomar todos sus campos
        r = s.get(BASE + REPORTS, params={"reports": rid, "phase": "Edit SQL"}, timeout=60)
        soup = BeautifulSoup(r.text, "html.parser")
        form = next((f for f in soup.find_all("form") if f.find("textarea", attrs={"name": "sql"})), None)
        if form is None:
            print(f"✗ {fname}: no encontré el form de edición (id {rid})"); continue
        fields = {}
        for el in form.find_all(["input", "textarea", "select"]):
            n = el.get("name")
            if n:
                fields[n] = el.get("value", "") if el.name != "textarea" else (el.text or "")
        # 2) reemplazar el SQL y guardar
        fields["sql"] = clean_sql((SQL_DIR / fname).read_text(encoding="utf-8"))
        fields["phase"] = "Update SQL"
        fields.setdefault("id", str(rid))
        r = s.post(BASE + REPORTS, data=fields, timeout=60)
        if "auth.tt" in r.text:
            print("✗ Sesión perdida."); return 2
        if 'class="dialog alert"' in r.text or "No SELECT" in r.text:
            snip = r.text[r.text.find("dialog alert"):][:160]
            print(f"✗ {fname} (id {rid}): rechazado … {snip}")
        else:
            print(f"✓ {fname} (id {rid}) actualizado.")
    print("\nListo. Los reportes ya usan el email de cualquiera de los 3 campos.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
