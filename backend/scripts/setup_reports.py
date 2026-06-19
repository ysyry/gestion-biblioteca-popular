#!/usr/bin/env python3
"""Configuración de UNA sola vez: crea los 4 reportes SQL en Koha y guarda sus IDs.

Lee las credenciales desde backend/.env (KOHA_USER / KOHA_PASSWORD) — NUNCA se
imprimen. Para cada archivo de sql/:
  1. Verifica si ya existe un reporte con el mismo nombre (no duplica).
  2. Si no existe, lo crea usando el formulario real de "Crear a partir de SQL".
  3. Resuelve el id del reporte y lo escribe en .env (REPORT_*_ID).

Uso (parado en backend/, con el venv activo y .env completo):
    python scripts/setup_reports.py

Es idempotente: se puede correr varias veces sin crear duplicados.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# Importa settings para leer .env sin exponer la contraseña.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import settings  # noqa: E402

LOGIN_PATH = "/cgi-bin/koha/mainpage.pl"
REPORTS_PATH = "/cgi-bin/koha/reports/guided_reports.pl"
LOGIN_MARKER = "auth.tt"

BACKEND_DIR = Path(__file__).resolve().parent.parent
SQL_DIR = BACKEND_DIR / "sql"
ENV_FILE = BACKEND_DIR / ".env"

# key → (archivo sql, nombre del reporte en Koha, variable .env)
REPORTS = [
    ("member_search", "01_member_search.sql", "appbiblio_member_search", "REPORT_MEMBER_SEARCH_ID"),
    ("member_loans",  "02_member_loans.sql",  "appbiblio_member_loans",  "REPORT_MEMBER_LOANS_ID"),
    ("loans_active",  "03_loans_active.sql",  "appbiblio_loans_active",  "REPORT_LOANS_ACTIVE_ID"),
    ("loans_overdue", "04_loans_overdue.sql", "appbiblio_loans_overdue", "REPORT_LOANS_OVERDUE_ID"),
    ("member_profile", "05_member_profile.sql", "appbiblio_member_profile", "REPORT_MEMBER_PROFILE_ID"),
    ("member_account", "06_member_account.sql", "appbiblio_member_account", "REPORT_MEMBER_ACCOUNT_ID"),
    ("member_history", "07_member_history.sql", "appbiblio_member_history", "REPORT_MEMBER_HISTORY_ID"),
    ("loans_contact", "08_loans_contact.sql", "appbiblio_loans_contact", "REPORT_LOANS_CONTACT_ID"),
]


def login(s: requests.Session, base: str) -> None:
    r = s.post(
        base + LOGIN_PATH,
        data={"userid": settings.koha_user, "password": settings.koha_password,
              "koha_login_context": "intranet"},
        timeout=30,
    )
    if LOGIN_MARKER in r.text:
        raise SystemExit("✗ Login rechazado por Koha. Revisá KOHA_USER/KOHA_PASSWORD en .env.")
    print(f"✓ Login OK como {settings.koha_user}.")


def list_saved(s: requests.Session, base: str) -> dict[str, int]:
    """Devuelve {nombre_reporte: id} de los reportes ya guardados."""
    r = s.get(base + REPORTS_PATH, params={"phase": "Use saved"}, timeout=60)
    soup = BeautifulSoup(r.text, "html.parser")
    found: dict[str, int] = {}
    for a in soup.find_all("a", href=re.compile(r"reports=(\d+)")):
        m = re.search(r"reports=(\d+)", a["href"])
        if not m:
            continue
        rid = int(m.group(1))
        # El nombre suele estar en la misma fila de la tabla.
        row = a.find_parent("tr")
        text = row.get_text(" ", strip=True) if row else a.get_text(strip=True)
        for _, _, name, _ in REPORTS:
            if name in text:
                found[name] = rid
    return found


def clean_sql(text: str) -> str:
    """Koha exige que el SQL empiece con SELECT: saca comentarios iniciales y el ';' final."""
    lines = text.splitlines()
    i = 0
    while i < len(lines) and (not lines[i].strip() or lines[i].lstrip().startswith("--")):
        i += 1
    sql = "\n".join(lines[i:]).strip()
    return sql[:-1].strip() if sql.endswith(";") else sql


def discover_create_form(s: requests.Session, base: str) -> tuple[str, dict[str, str]]:
    """Lee el formulario de 'Crear a partir de SQL' y devuelve (action, campos_con_defaults)."""
    r = s.get(base + REPORTS_PATH, params={"phase": "Create report from SQL"}, timeout=60)
    soup = BeautifulSoup(r.text, "html.parser")
    # Buscamos el form que contiene un textarea llamado 'sql'.
    form = next((f for f in soup.find_all("form") if f.find("textarea", attrs={"name": "sql"})), None)
    if form is None:
        raise SystemExit("✗ No encontré el formulario de 'Crear a partir de SQL'. "
                         "Pegame el HTML de esa página y lo ajusto.")
    action = form.get("action") or REPORTS_PATH
    if action.startswith("/"):
        action = base + action
    elif not action.startswith("http"):
        action = base + REPORTS_PATH
    # Tomamos TODOS los campos con sus valores por defecto (no solo los ocultos).
    fields: dict[str, str] = {}
    for el in form.find_all(["input", "textarea", "select"]):
        n = el.get("name")
        if not n:
            continue
        fields[n] = el.get("value", "") if el.name != "textarea" else (el.text or "")
    print(f"  Campos del formulario detectados: {sorted(fields)}")
    return action, fields


def create_report(s: requests.Session, base: str, action: str, fields: dict[str, str],
                  name: str, sql: str) -> None:
    payload = dict(fields)
    payload.update({
        "reportname": name,
        "notes": "Creado por biblioteca-app (POC). Solo lectura.",
        "sql": clean_sql(sql),
        "public": "0",
        "phase": "Save Report",
        "submit": "Guardar informe",
    })
    r = s.post(action, data=payload, timeout=60)
    if LOGIN_MARKER in r.text:
        raise SystemExit("✗ La sesión se perdió al guardar. Reintentá.")
    if "No SELECT" in r.text or 'class="dialog alert"' in r.text:
        snippet = r.text[r.text.find("dialog alert"):][:200]
        print(f"  ✗ Koha rechazó '{name}': …{snippet}")
        return
    print(f"  → '{name}' guardado (HTTP {r.status_code}).")


def write_env_ids(ids: dict[str, int]) -> None:
    """Escribe/actualiza las líneas REPORT_*_ID en .env sin tocar el resto."""
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    by_var = {var: ids[key] for key, _, _, var in REPORTS if key in ids}
    out, seen = [], set()
    for ln in lines:
        key = ln.split("=", 1)[0].strip() if "=" in ln else ""
        if key in by_var:
            out.append(f"{key}={by_var[key]}")
            seen.add(key)
        else:
            out.append(ln)
    for var, val in by_var.items():
        if var not in seen:
            out.append(f"{var}={val}")
    ENV_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"✓ IDs escritos en {ENV_FILE}: {by_var}")


def main() -> int:
    if not settings.koha_user or not settings.koha_password:
        print("✗ Faltan KOHA_USER / KOHA_PASSWORD en backend/.env.")
        return 2
    base = settings.koha_base_url.rstrip("/")
    s = requests.Session()

    login(s, base)
    existing = list_saved(s, base)
    print(f"Reportes ya existentes de la app: {existing or 'ninguno'}")

    action, hidden = discover_create_form(s, base)

    for _, fname, name, _ in REPORTS:
        if name in existing:
            print(f"= '{name}' ya existe (id {existing[name]}), no se recrea.")
            continue
        sql = (SQL_DIR / fname).read_text(encoding="utf-8")
        print(f"+ Creando '{name}' desde {fname} …")
        create_report(s, base, action, hidden, name, sql)

    # Resolver IDs (incluye los recién creados).
    final = list_saved(s, base)
    ids = {key: final[name] for key, _, name, _ in REPORTS if name in final}
    missing = [name for _, _, name, _ in REPORTS if name not in final]
    if missing:
        print(f"⚠ No pude resolver el id de: {missing}. Revisá en la intranet o pegame la salida.")
    if ids:
        write_env_ids(ids)
    print("\nListo. Si quedaron los 4 IDs, levantá la API: uvicorn app.main:app --port 8000")
    return 0


if __name__ == "__main__":
    sys.exit(main())
