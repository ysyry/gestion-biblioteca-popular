"""Prueba de concepto de ARCA — valida el circuito de facturación de punta a punta.

EN CONSTRUCCIÓN: este script es justamente el paso que falta correr. Hoy no se
puede completar porque en credentials/ está el .csr y la .key, pero todavía no
el .crt que emite ARCA.

Uso (desde backend/):
    ./.venv/bin/python scripts/arca_poc.py            # diagnóstico + dummy + login + último nro
    ./.venv/bin/python scripts/arca_poc.py --emitir   # además emite UNA Factura C de prueba

Por defecto trabaja en HOMOLOGACIÓN (ARCA_PROD=false): los CAE son de mentira y no
tienen validez fiscal. Requiere el certificado y la clave en backend/credentials/
(arca_homo.crt / arca_homo.key).
"""
import sys
from datetime import date
from pathlib import Path

# Permitir importar el paquete `app` corriendo el script directamente.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import arca  # noqa: E402
from app.config import settings  # noqa: E402


def main() -> int:
    emitir = "--emitir" in sys.argv
    print("=" * 60)
    print("  PoC ARCA — Facturación Electrónica")
    print("=" * 60)

    st = arca.estado()
    print(f"Entorno      : {st['entorno']}  (ARCA_PROD={st['prod']})")
    print(f"CUIT emisor  : {st['cuit'] or '(sin configurar)'}")
    print(f"Punto de vta : {st['pto_vta'] or '(sin configurar)'}")
    print(f"Certificado  : {'OK' if st['cert_ok'] else 'FALTA'}  ({st['cert_path']})")
    print(f"Clave priv.  : {'OK' if st['key_ok'] else 'FALTA'}  ({st['key_path']})")
    print("-" * 60)

    if not arca.configured():
        print("\n⚠  Falta configuración. Revisá que estén el CUIT, el punto de venta,")
        print("   el certificado (arca_homo.crt) y la clave (arca_homo.key) en credentials/.")
        return 1

    # 1) FEDummy — ¿están vivos los servidores de ARCA? (no requiere login)
    print("\n[1] FEDummy (estado de servidores)...")
    try:
        d = arca.dummy()
        print(f"    AppServer={d['AppServer']}  DbServer={d['DbServer']}  AuthServer={d['AuthServer']}")
    except Exception as e:
        print(f"    ✗ Falló: {e}")
        return 2

    # 2) WSAA — login y obtención de token
    print("\n[2] WSAA loginCms (autenticación)...")
    try:
        token, sign = arca.get_token_sign(force=True)
        print(f"    ✓ Token obtenido ({len(token)} chars), sign ({len(sign)} chars).")
    except Exception as e:
        print(f"    ✗ Falló: {e}")
        return 3

    # 3) WSFE — último comprobante autorizado
    print("\n[3] FECompUltimoAutorizado (último N° de Factura C)...")
    try:
        ultimo = arca.ultimo_autorizado()
        print(f"    ✓ Último autorizado en pto vta {settings.arca_pto_vta}: N° {ultimo}")
        print(f"      → la próxima factura sería la N° {ultimo + 1}")
    except Exception as e:
        print(f"    ✗ Falló: {e}")
        return 4

    # 4) (Opcional) Emitir una Factura C de prueba
    if emitir:
        print("\n[4] FECAESolicitar (emitiendo Factura C de PRUEBA por $100)...")
        try:
            r = arca.emitir_factura_c(
                importe=100.0,
                doc_tipo=arca.DOC_TIPO_CF,
                doc_nro=0,
                fecha=date.today(),
            )
            print(f"    ✓ CAE OBTENIDO: {r.cae}")
            print(f"      Vto CAE     : {r.cae_vto}")
            print(f"      Comprobante : N° {r.nro} (pto vta {r.pto_vta}, tipo {r.cbte_tipo})")
            print(f"      Resultado   : {r.resultado}  Importe: ${r.importe}")
            if r.observaciones:
                print(f"      Observaciones: {r.observaciones}")
        except Exception as e:
            print(f"    ✗ Falló: {e}")
            return 5
    else:
        print("\n[4] (omitido) Pasá --emitir para emitir una Factura C de prueba.")

    print("\n" + "=" * 60)
    print("  ✓ Circuito validado.")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
