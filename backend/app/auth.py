"""Autenticación y sesión. Hay dos maneras de entrar, y conviven:

1. **Bibliotecarias — con su usuario de Koha** (como desde el día uno).
   La app inicia sesión real en Koha con esas credenciales; si Koha las acepta,
   guarda esa sesión y emite un token propio. La contraseña nunca se persiste.
   Rol: `bibliotecaria`.

2. **Comisión directiva y subcomisiones — con un usuario propio de la app**
   (ver `usuarios.py`). Esta gente no tiene usuario de Koha.
   Rol: el que tenga cargado el usuario (`comision` o `subcomision`).

Para decidir por dónde va cada login: si el nombre existe como usuario propio de la
app, se valida contra la app; si no, se prueba contra Koha. Sin ambigüedad.

**Acceso a Koha de quien no tiene usuario de Koha.** La comisión directiva ve los
mismos datos que las bibliotecarias, pero no tiene con qué entrar a Koha. Para eso se
usa una **cuenta de servicio** (`KOHA_USER` / `KOHA_PASSWORD`), compartida y creada una
sola vez. Queda registrado en el log qué usuario de la app hizo cada consulta, para no
perder la trazabilidad. Quien no tiene el permiso `koha` nunca llega a esa cuenta.

Nota: `SESIONES` es un diccionario en memoria (un solo proceso). Si el backend se
reinicia, hay que volver a entrar — anotado como mejora futura (Redis).
"""
from __future__ import annotations

import asyncio
import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from . import permisos, usuarios
from .config import settings
from .koha.client import KohaAuthError, KohaClient
from .koha.reports import KohaRepository

logger = logging.getLogger("auth")

ALGORITHM = "HS256"
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


@dataclass
class Sesion:
    """Lo que la app sabe de quien está adentro, mientras dura su sesión."""
    sid: str
    usuario: str                       # con qué nombre entró
    nombre: str                        # cómo se llama la persona
    rol: str
    uid: str | None = None             # id del usuario propio de la app (None si es de Koha)
    subcomision: str = ""
    koha: KohaClient | None = None     # sesión propia de Koha (solo las bibliotecarias)
    permisos: set[str] = field(default_factory=set)

    @property
    def es_de_koha(self) -> bool:
        return self.koha is not None

    def puede(self, permiso: str) -> bool:
        return permiso in self.permisos


# sid -> Sesion
SESIONES: dict[str, Sesion] = {}

# Compatibilidad: código viejo que miraba `SESSIONS`.
SESSIONS = SESIONES

# Cuenta de servicio de Koha, compartida por los usuarios propios de la app.
_koha_servicio: KohaClient | None = None
_koha_servicio_lock = asyncio.Lock()


def _emitir_token(s: Sesion) -> str:
    expira = datetime.now(timezone.utc) + timedelta(minutes=settings.app_token_expire_minutes)
    return jwt.encode(
        {"sub": s.usuario, "sid": s.sid, "rol": s.rol, "uid": s.uid, "exp": expira},
        settings.app_secret_key,
        algorithm=ALGORITHM,
    )


def _respuesta(s: Sesion) -> dict:
    return {
        "access_token": _emitir_token(s),
        "token_type": "bearer",
        "username": s.usuario,
        "nombre": s.nombre,
        "rol": s.rol,
        "rol_etiqueta": permisos.ETIQUETAS.get(s.rol, s.rol),
        "subcomision": s.subcomision,
        "permisos": sorted(s.permisos),
        "secciones": permisos.secciones_de(s.rol),
        "menu": permisos.menu_de(s.rol),
    }


async def authenticate(username: str, password: str) -> dict:
    """Valida credenciales (usuario propio de la app o de Koha) y abre sesión."""
    username = (username or "").strip()
    sid = secrets.token_urlsafe(24)

    # ── 1. Usuario propio de la app ─────────────────────────────────────────
    if usuarios.existe(username):
        u = usuarios.verificar(username, password)
        if u is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Usuario o contraseña incorrectos.",
            )
        s = Sesion(
            sid=sid,
            usuario=u["usuario"],
            nombre=u.get("nombre") or u["usuario"],
            rol=u.get("rol", ""),
            uid=u.get("id"),
            subcomision=u.get("subcomision", ""),
            permisos=permisos.permisos_de(u.get("rol", "")),
        )
        SESIONES[sid] = s
        usuarios.registrar_acceso(s.uid)
        logger.info("Login OK (app): %s [%s]", s.usuario, s.rol)
        return _respuesta(s)

    # ── 2. Usuario de Koha (bibliotecarias) ─────────────────────────────────
    client = KohaClient(settings.koha_base_url, username, password)
    try:
        await client.login()
    except KohaAuthError as exc:
        await client.aclose()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    s = Sesion(
        sid=sid,
        usuario=username,
        nombre=username,
        rol="bibliotecaria",
        koha=client,
        permisos=permisos.permisos_de("bibliotecaria"),
    )
    SESIONES[sid] = s
    logger.info("Login OK (koha): %s [bibliotecaria]", username)
    return _respuesta(s)


async def logout(sid: str) -> None:
    s = SESIONES.pop(sid, None)
    if s and s.koha:
        await s.koha.aclose()


def _decode(token: str) -> dict:
    try:
        return jwt.decode(token, settings.app_secret_key, algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido o expirado.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


# ── Dependencias ────────────────────────────────────────────────────────────
async def get_session(token: str = Depends(oauth2_scheme)) -> Sesion:
    """Resuelve el token a la sesión abierta. Es la base de todo lo demás."""
    sid = _decode(token).get("sid")
    s = SESIONES.get(sid) if sid else None
    if s is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sesión no encontrada (el servidor se reinició o expiró). Volvé a entrar.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if s.uid:
        # Usuario propio de la app: lo que se le cambie vale ya, no cuando vuelva a entrar.
        # Si lo desactivaron o lo borraron, la sesión abierta se corta acá mismo.
        u = usuarios.obtener(s.uid)
        if u is None or not u.get("activo"):
            SESIONES.pop(sid, None)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Tu usuario ya no tiene acceso. Consultá con la biblioteca.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if u.get("rol", "") != s.rol or u.get("subcomision", "") != s.subcomision:
            s.rol, s.subcomision = u.get("rol", ""), u.get("subcomision", "")
            s.permisos = permisos.permisos_de(s.rol)
    return s


async def _cliente_de_servicio() -> KohaClient:
    """Sesión de Koha compartida, para los usuarios que no tienen una propia."""
    global _koha_servicio
    async with _koha_servicio_lock:
        if _koha_servicio is None:
            if not (settings.koha_user and settings.koha_password):
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Falta configurar la cuenta de servicio de Koha "
                           "(KOHA_USER / KOHA_PASSWORD) para los usuarios de la app.",
                )
            client = KohaClient(settings.koha_base_url, settings.koha_user, settings.koha_password)
            try:
                await client.login()
            except KohaAuthError as exc:
                await client.aclose()
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=f"La cuenta de servicio de Koha no pudo entrar: {exc}",
                ) from exc
            _koha_servicio = client
    return _koha_servicio


async def get_current_client(s: Sesion = Depends(get_session)) -> KohaClient:
    """El cliente de Koha que le corresponde a esta sesión."""
    permisos.exigir(s.rol, permisos.KOHA)
    if s.koha is not None:
        return s.koha
    logger.info("Koha vía cuenta de servicio, a pedido de %s [%s]", s.usuario, s.rol)
    return await _cliente_de_servicio()


async def get_repository(client: KohaClient = Depends(get_current_client)) -> KohaRepository:
    """Dependencia para los endpoints: repositorio ligado a la sesión actual."""
    return KohaRepository(client)


async def get_current_username(s: Sesion = Depends(get_session)) -> str:
    return s.usuario


def requiere(permiso: str):
    """Dependencia que exige un permiso. Uso:

        @router.get("/inventario", dependencies=[Depends(requiere(permisos.INVENTARIO_VER))])
    """
    async def _guarda(s: Sesion = Depends(get_session)) -> Sesion:
        permisos.exigir(s.rol, permiso)
        return s
    return _guarda
