#!/usr/bin/env python3
"""Prueba aislada del envío de mails (sin Koha ni login).

Usa el MISMO servicio que la app (app/mail.py) y la config de backend/.env.

Uso:
  # 1) Previsualizar (NO envía nada, muestra el mail ya armado):
  python3 scripts/test_mail.py

  # 2) Enviar de verdad UN mail de prueba a tu propia casilla:
  python3 scripts/test_mail.py --to vos@gmail.com

  # 3) Probar la combinación de variables con otro nombre:
  python3 scripts/test_mail.py --to vos@gmail.com --nombre Ana --apellido Perez
"""
import argparse
import asyncio
import sys
from pathlib import Path

# permite importar el paquete `app` corriendo desde backend/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import mail            # noqa: E402
from app.config import settings  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Prueba de envío de mail")
    p.add_argument("--to", help="Dirección destino real. Si se omite, solo previsualiza (dry-run).")
    p.add_argument("--nombre", default="Socia de Prueba")
    p.add_argument("--apellido", default="Bayer")
    p.add_argument("--carnet", default="00000")
    args = p.parse_args()

    print("── Configuración SMTP detectada ──────────────────────────")
    print(f"  host........: {settings.smtp_host or '(vacío)'}")
    print(f"  port........: {settings.smtp_port}")
    print(f"  use_tls.....: {settings.smtp_use_tls}")
    print(f"  user........: {settings.smtp_user or '(vacío)'}")
    print(f"  from........: {settings.smtp_from or settings.smtp_user or '(vacío)'}")
    print(f"  from_name...: {settings.smtp_from_name}")
    print(f"  dry_run env.: {settings.mail_dry_run}")
    print("──────────────────────────────────────────────────────────")

    real_send = args.to is not None
    if real_send:
        faltan = [k for k, v in {
            "SMTP_USER": settings.smtp_user,
            "SMTP_PASSWORD": settings.smtp_password,
        }.items() if not v]
        if faltan:
            print(f"\n⚠️  Falta cargar en backend/.env: {', '.join(faltan)}")
            print("   (cargá tu Gmail y la contraseña de aplicación y volvé a probar)")
            return 1

    subject = "Recordatorio de la Biblioteca Popular Osvaldo Bayer"
    body = (
        "Hola {{nombre}} {{apellido}},\n\n"
        "Te escribimos desde la Biblioteca Popular Osvaldo Bayer (carnet {{carnet}}).\n"
        "Este es un mensaje de prueba del nuevo sistema de avisos.\n\n"
        "Saludos,\n"
        "Comisión Directiva\n"
        "Biblioteca Popular Osvaldo Bayer — Villa La Angostura"
    )
    recipients = [{
        "email": args.to or "destino@ejemplo.org",
        "vars": {"nombre": args.nombre, "apellido": args.apellido, "carnet": args.carnet},
        "subject": None,
        "body": None,
    }]

    # Si pasaste --to, forzamos envío real a esa dirección (test_to).
    dry_run = not real_send
    res = asyncio.run(mail.send_campaign(
        subject_tpl=subject,
        body_tpl=body,
        recipients=recipients,
        dry_run=dry_run,
        test_to=args.to,
    ))

    print("\n── Resultado ─────────────────────────────────────────────")
    print(f"  modo......: {'DRY-RUN (no se envió)' if dry_run else 'ENVÍO REAL'}")
    print(f"  total.....: {res['total']}  preparados: {res['preparados']}  "
          f"enviados: {res['enviados']}  simulados: {res['simulados']}")
    for r in res["resultados"]:
        print(f"  - {r.get('email')}: {r.get('status')}  {r.get('detail','')}")
    if dry_run:
        print("\n  (Para enviar de verdad: agregá  --to tu-correo@gmail.com)")
    print("──────────────────────────────────────────────────────────")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
