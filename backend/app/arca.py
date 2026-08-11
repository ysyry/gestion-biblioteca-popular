"""Facturación electrónica de ARCA (ex-AFIP) — Factura C vía WSFEv1.

╔══════════════════════════════════════════════════════════════════════════╗
║  EN CONSTRUCCIÓN — todavía NO se usa desde la app.                       ║
║                                                                          ║
║  El motor está escrito y con tests, pero falta:                          ║
║    · el certificado .crt de ARCA (hoy solo tenemos el .csr y la .key);   ║
║    · probar el circuito completo en homologación (scripts/arca_poc.py);  ║
║    · los endpoints y la pantalla para emitir desde la app.               ║
║                                                                          ║
║  Nada de esto se ejecuta solo: ARCA_ENABLED viene en false y ningún      ║
║  endpoint importa este módulo. Se puede leer y tocar sin romper nada.    ║
╚══════════════════════════════════════════════════════════════════════════╝

La biblioteca es una **asociación civil exenta**, así que el único comprobante que
corresponde es la **Factura C (código 011)**: no discrimina IVA, se informan solo los
totales. El detalle de ítems ("cuotas hasta julio 2026") va en el PDF que imprimimos
nosotros, NO se manda al web service.

El circuito son dos servicios SOAP encadenados:

1. **WSAA** — con el certificado digital firmamos un "ticket de acceso" (TRA) en formato
   PKCS#7 y ARCA nos devuelve un ``token`` + ``sign`` que valen ~12 h. Los cacheamos en
   ``storage`` para no re-loguear en cada emisión.
2. **WSFEv1** — con ese token pedimos el CAE (Código de Autorización Electrónico), que es
   lo que hace válida la factura.

Hay dos entornos: **homologación** (pruebas, CAE de mentira) y **producción** (real). Se
elige con ``settings.arca_prod``. El certificado/clave son secretos: viven en
``backend/credentials/`` (gitignoreado).

La firma WSAA sigue el enfoque de la librería ``arca_arg`` (López Briega, MIT), verificado
contra la documentación de ARCA, pero acá está integrado a nuestros patrones (config de
``settings``, token en ``storage``, ``zoneinfo`` en vez de ``pytz``, sin estado global).

Las llamadas SOAP son **bloqueantes**: desde código async, invocalas con
``asyncio.to_thread(...)``.
"""
from __future__ import annotations

import base64
import logging
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import settings
from . import storage

logger = logging.getLogger(__name__)

# ── Constantes ARCA ─────────────────────────────────────────────────────────
TZ = ZoneInfo("America/Argentina/Buenos_Aires")
SERVICE = "wsfe"
CBTE_FACTURA_C = 11           # tipo de comprobante: Factura C
CONCEPTO_SERVICIOS = 2        # Concepto: 2 = Servicios (cuota social)
DOC_TIPO_DNI = 96             # tipo de documento del receptor: DNI
DOC_TIPO_CF = 99             # Consumidor Final sin identificar
MONEDA_PESOS = "PES"
# Margen de seguridad: renovamos el token 10 min antes de su vencimiento real.
_TA_SAFETY_SECONDS = 600

WSDL_WSAA = {
    False: "https://wsaahomo.afip.gov.ar/ws/services/LoginCms?wsdl",
    True: "https://wsaa.afip.gov.ar/ws/services/LoginCms?wsdl",
}
WSDL_WSFE = {
    False: "https://wswhomo.afip.gov.ar/wsfev1/service.asmx?WSDL",
    True: "https://servicios1.afip.gov.ar/wsfev1/service.asmx?WSDL",
}

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_CRED_DIR = _BACKEND_DIR / "credentials"


class ArcaError(Exception):
    """Error de negocio de ARCA (rechazo, observación, o configuración faltante)."""


@dataclass
class CAEResult:
    """Resultado de una emisión exitosa."""
    cae: str
    cae_vto: str            # 'YYYYMMDD' que devuelve ARCA
    nro: int                # número de comprobante autorizado
    pto_vta: int
    cbte_tipo: int
    resultado: str          # 'A' aprobado, 'P' parcial
    fecha: str              # 'YYYYMMDD' de emisión
    importe: float
    observaciones: list[str]


# ── Rutas de certificado / clave ────────────────────────────────────────────
def _env_tag() -> str:
    return "prod" if settings.arca_prod else "homo"


def cert_path() -> Path:
    if settings.arca_cert_path:
        return Path(settings.arca_cert_path)
    return _CRED_DIR / f"arca_{_env_tag()}.crt"


def key_path() -> Path:
    if settings.arca_key_path:
        return Path(settings.arca_key_path)
    return _CRED_DIR / f"arca_{_env_tag()}.key"


def estado() -> dict:
    """Diagnóstico de configuración (para mostrar en la app, sin secretos)."""
    return {
        "enabled": settings.arca_enabled,
        "prod": settings.arca_prod,
        "entorno": "producción" if settings.arca_prod else "homologación",
        "cuit": settings.arca_cuit,
        "pto_vta": settings.arca_pto_vta,
        "cert_ok": cert_path().exists(),
        "key_ok": key_path().exists(),
        "cert_path": str(cert_path()),
        "key_path": str(key_path()),
    }


def configured() -> bool:
    """True si hay lo mínimo para emitir: CUIT, punto de venta, cert y clave."""
    return bool(
        settings.arca_cuit
        and settings.arca_pto_vta
        and cert_path().exists()
        and key_path().exists()
    )


def _require_configured() -> None:
    if not settings.arca_cuit:
        raise ArcaError("Falta ARCA_CUIT en la configuración.")
    if not settings.arca_pto_vta:
        raise ArcaError("Falta ARCA_PTO_VTA (punto de venta) en la configuración.")
    if not cert_path().exists():
        raise ArcaError(f"No se encuentra el certificado en {cert_path()}.")
    if not key_path().exists():
        raise ArcaError(f"No se encuentra la clave privada en {key_path()}.")


# ── WSAA: autenticación ─────────────────────────────────────────────────────
def _ta_storage_key() -> str:
    return f"arca_ta_{SERVICE}_{_env_tag()}"


def _build_tra() -> bytes:
    """Arma el XML del Ticket Request Access (TRA) para el servicio wsfe."""
    now = datetime.now(TZ)
    exp = now + timedelta(hours=12)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<loginTicketRequest version="1.0">'
        "<header>"
        f"<uniqueId>{int(now.timestamp())}</uniqueId>"
        f"<generationTime>{now.strftime('%Y-%m-%dT%H:%M:%S')}</generationTime>"
        f"<expirationTime>{exp.strftime('%Y-%m-%dT%H:%M:%S')}</expirationTime>"
        "</header>"
        f"<service>{SERVICE}</service>"
        "</loginTicketRequest>"
    ).encode("utf-8")


def _sign_tra(tra_xml: bytes) -> str:
    """Firma el TRA en PKCS#7 (CMS) DER y lo devuelve en base64, como pide ARCA."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.serialization import pkcs7

    private_key = serialization.load_pem_private_key(key_path().read_bytes(), password=None)
    cert = x509.load_pem_x509_certificate(cert_path().read_bytes())
    signed = (
        pkcs7.PKCS7SignatureBuilder()
        .set_data(tra_xml)
        .add_signer(cert, private_key, hashes.SHA256())
        .sign(serialization.Encoding.DER, [pkcs7.PKCS7Options.NoCapabilities])
    )
    return base64.b64encode(signed).decode("ascii")


def _parse_ta(xml_response: str) -> dict:
    """Extrae token/sign/expiración del XML de respuesta del loginCms."""
    root = ET.fromstring(xml_response)
    token = root.findtext(".//token")
    sign = root.findtext(".//sign")
    exp_str = root.findtext(".//expirationTime")
    # expirationTime viene ISO con offset (ej: 2026-08-04T12:00:00.000-03:00).
    exp_dt = datetime.fromisoformat(exp_str)
    return {
        "token": token,
        "sign": sign,
        "expires": exp_dt.timestamp() - _TA_SAFETY_SECONDS,
    }


def _ta_valido(ta: dict | None) -> bool:
    return bool(ta and ta.get("token") and ta.get("expires", 0) > time.time())


def get_token_sign(force: bool = False) -> tuple[str, str]:
    """Devuelve (token, sign) vigentes; renueva contra WSAA si hace falta."""
    _require_configured()
    key = _ta_storage_key()
    ta = None if force else storage.get(key)
    if _ta_valido(ta):
        return ta["token"], ta["sign"]

    from zeep import Client

    tra = _build_tra()
    cms = _sign_tra(tra)
    client = Client(WSDL_WSAA[settings.arca_prod])
    try:
        resp = client.service.loginCms(cms)
    except Exception as e:  # zeep envuelve el fault del server
        raise ArcaError(f"WSAA login falló: {e}") from e
    ta = _parse_ta(resp)
    storage.set(key, ta)
    logger.info("ARCA: TA renovado (%s), vence en ~%d min", _env_tag(),
                int((ta["expires"] - time.time()) / 60))
    return ta["token"], ta["sign"]


# ── WSFEv1: facturación ─────────────────────────────────────────────────────
@lru_cache(maxsize=2)
def _wsfe_client(prod: bool):
    """Cliente zeep del WSFE, cacheado por entorno (cargar el WSDL es costoso)."""
    from zeep import Client
    return Client(WSDL_WSFE[prod])


def _auth() -> dict:
    token, sign = get_token_sign()
    return {"Token": token, "Sign": sign, "Cuit": int(settings.arca_cuit)}


def dummy() -> dict:
    """FEDummy: estado de los servidores de ARCA (no requiere autenticación)."""
    client = _wsfe_client(settings.arca_prod)
    r = client.service.FEDummy()
    return {"AppServer": r.AppServer, "DbServer": r.DbServer, "AuthServer": r.AuthServer}


def _check_errors(resp) -> None:
    errs = getattr(resp, "Errors", None)
    if errs and getattr(errs, "Err", None):
        msgs = [f"{e.Code}: {e.Msg}" for e in errs.Err]
        raise ArcaError("ARCA devolvió errores: " + " | ".join(msgs))


def ultimo_autorizado(cbte_tipo: int = CBTE_FACTURA_C, pto_vta: int | None = None) -> int:
    """Último número de comprobante autorizado para el punto de venta y tipo dados."""
    _require_configured()
    pv = pto_vta or settings.arca_pto_vta
    client = _wsfe_client(settings.arca_prod)
    resp = client.service.FECompUltimoAutorizado(Auth=_auth(), PtoVta=pv, CbteTipo=cbte_tipo)
    _check_errors(resp)
    return int(resp.CbteNro)


def _fmt_fecha(d: date) -> str:
    return d.strftime("%Y%m%d")


def emitir_factura_c(
    importe: float,
    *,
    doc_nro: str | int = 0,
    doc_tipo: int = DOC_TIPO_CF,
    fecha: date | None = None,
    serv_desde: date | None = None,
    serv_hasta: date | None = None,
    vto_pago: date | None = None,
    pto_vta: int | None = None,
) -> CAEResult:
    """Emite una **Factura C** a consumidor final y devuelve el CAE.

    Solo se informan totales (WSFEv1 "sin detalle"): el texto del ítem va en el PDF.
    Para Factura C no se manda IVA: ``ImpNeto`` lleva el total e ``ImpIVA`` = 0.

    Args:
        importe: importe total en pesos.
        doc_nro: DNI/CUIT del receptor (0 para consumidor final sin identificar).
        doc_tipo: 96 = DNI, 80 = CUIT, 99 = consumidor final sin identificar.
        fecha: fecha de emisión (por defecto hoy en Buenos Aires).
        serv_desde/serv_hasta/vto_pago: período facturado y vencimiento de pago
            (obligatorios cuando el concepto es Servicios; por defecto = fecha).
        pto_vta: punto de venta (por defecto ``settings.arca_pto_vta``).
    """
    _require_configured()
    if importe <= 0:
        raise ArcaError("El importe debe ser mayor a 0.")

    pv = pto_vta or settings.arca_pto_vta
    hoy = fecha or datetime.now(TZ).date()
    sd = serv_desde or hoy
    sh = serv_hasta or hoy
    vp = vto_pago or hoy
    nro = ultimo_autorizado(CBTE_FACTURA_C, pv) + 1
    imp = round(float(importe), 2)

    det = {
        "Concepto": CONCEPTO_SERVICIOS,
        "DocTipo": doc_tipo,
        "DocNro": int(doc_nro or 0),
        "CbteDesde": nro,
        "CbteHasta": nro,
        "CbteFch": _fmt_fecha(hoy),
        "ImpTotal": imp,
        "ImpTotConc": 0,
        "ImpNeto": imp,       # Factura C: el neto es el total (sin IVA)
        "ImpOpEx": 0,
        "ImpTrib": 0,
        "ImpIVA": 0,
        "FchServDesde": _fmt_fecha(sd),
        "FchServHasta": _fmt_fecha(sh),
        "FchVtoPago": _fmt_fecha(vp),
        "MonId": MONEDA_PESOS,
        "MonCotiz": 1,
    }
    req = {
        "FeCabReq": {"CantReg": 1, "PtoVta": pv, "CbteTipo": CBTE_FACTURA_C},
        "FeDetReq": {"FECAEDetRequest": [det]},
    }

    client = _wsfe_client(settings.arca_prod)
    resp = client.service.FECAESolicitar(Auth=_auth(), FeCAEReq=req)
    _check_errors(resp)

    cab = resp.FeCabResp
    detr = resp.FeDetResp.FECAEDetResponse[0]
    obs = []
    if getattr(detr, "Observaciones", None) and getattr(detr.Observaciones, "Obs", None):
        obs = [f"{o.Code}: {o.Msg}" for o in detr.Observaciones.Obs]

    if cab.Resultado == "R" or not detr.CAE:
        raise ArcaError(
            "ARCA rechazó el comprobante"
            + (": " + " | ".join(obs) if obs else "") + "."
        )

    return CAEResult(
        cae=str(detr.CAE),
        cae_vto=str(detr.CAEFchVto),
        nro=int(detr.CbteDesde),
        pto_vta=pv,
        cbte_tipo=CBTE_FACTURA_C,
        resultado=str(cab.Resultado),
        fecha=_fmt_fecha(hoy),
        importe=imp,
        observaciones=obs,
    )
