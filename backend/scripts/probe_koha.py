#!/usr/bin/env python3
"""Sondeo de Koha — verifica login y el formato real de svc/report.

Koha 3.x no tiene API REST, así que confirmamos a mano que:
  1. El login de staff funciona con tus credenciales.
  2. svc/report devuelve JSON y con qué forma (lista de listas vs lista de objetos).

Uso:
    # credenciales por variables de entorno (recomendado)
    export KOHA_BASE_URL=http://3169.bepe.ar:8080
    export KOHA_USER=tu_usuario
    export KOHA_PASSWORD=tu_password
    python scripts/probe_koha.py <ID_DEL_REPORTE> [param1 param2 ...]

    # o pasando todo por argumentos
    python scripts/probe_koha.py <ID_DEL_REPORTE> --base http://... --user u --password p

Salida: estado del login, HTTP del reporte, forma detectada del JSON y una muestra.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import requests

LOGIN_PATH = "/cgi-bin/koha/mainpage.pl"
REPORT_PATH = "/cgi-bin/koha/svc/report"
LOGIN_MARKER = "auth.tt"


def main() -> int:
    ap = argparse.ArgumentParser(description="Sondeo de login + svc/report en Koha.")
    ap.add_argument("report_id", help="id del reporte SQL guardado en la intranet")
    ap.add_argument("params", nargs="*", help="parámetros del reporte, en orden")
    ap.add_argument("--base", default=os.environ.get("KOHA_BASE_URL", "http://3169.bepe.ar:8080"))
    ap.add_argument("--user", default=os.environ.get("KOHA_USER", ""))
    ap.add_argument("--password", default=os.environ.get("KOHA_PASSWORD", ""))
    args = ap.parse_args()

    if not args.user or not args.password:
        print("ERROR: faltan credenciales (KOHA_USER / KOHA_PASSWORD o --user/--password).")
        return 2

    base = args.base.rstrip("/")
    s = requests.Session()

    # 1) Login
    print(f"→ Login en {base} como {args.user} …")
    r = s.post(
        base + LOGIN_PATH,
        data={"userid": args.user, "password": args.password, "koha_login_context": "intranet"},
        timeout=30,
    )
    if LOGIN_MARKER in r.text:
        print("✗ Login RECHAZADO (la respuesta sigue siendo la página de ingreso).")
        print("  Revisá usuario/contraseña y que el usuario tenga acceso a la intranet.")
        return 1
    print("✓ Login OK.")

    # 2) Ejecutar el reporte
    query = [("id", str(args.report_id))] + [("param_name", p) for p in args.params]
    print(f"→ svc/report id={args.report_id} params={args.params} …")
    r = s.get(base + REPORT_PATH, params=query, timeout=60)
    print(f"  HTTP {r.status_code}, content-type: {r.headers.get('content-type')}")

    if LOGIN_MARKER in r.text:
        print("✗ Devolvió la página de login: la sesión no se mantuvo o falta permiso de reportes.")
        return 1

    # 3) Analizar el JSON
    try:
        data = r.json()
    except ValueError:
        print("✗ La respuesta NO es JSON. Primeros 500 caracteres:")
        print(r.text[:500])
        return 1

    print(f"✓ JSON recibido. Tipo raíz: {type(data).__name__}")
    if isinstance(data, list):
        print(f"  Filas: {len(data)}")
        if data:
            first = data[0]
            kind = "lista/array (mapear por orden de columnas)" if isinstance(first, (list, tuple)) \
                else "objeto/dict (ya trae nombres de columna)" if isinstance(first, dict) \
                else type(first).__name__
            print(f"  Forma de fila: {kind}")
            print("  Muestra (primeras filas):")
            print(json.dumps(data[:3], ensure_ascii=False, indent=2))
    else:
        print("  Estructura inesperada; muestra:")
        print(json.dumps(data, ensure_ascii=False, indent=2)[:1000])

    print("\nListo. Pasame esta salida y afino el mapeo de columnas si hace falta.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
