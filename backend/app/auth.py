"""Autenticación: cada bibliotecaria entra con SUS credenciales de Koha.

Flujo:
  1. POST /api/auth/login con {username, password} de Koha.
  2. La app intenta iniciar sesión en Koha con esas credenciales (KohaClient.login()).
     - Si Koha las rechaza -> 401.
     - Si las acepta -> guardamos el KohaClient autenticado en SESSIONS[sid] y
       emitimos un JWT propio que lleva ese sid. La contraseña NO se guarda.
  3. Cada pedido posterior manda el JWT (Bearer). Resolvemos sid -> KohaClient y
     ejecutamos los reportes con la sesión real de esa bibliotecaria.

Nota POC: SESSIONS es un dict en memoria (un solo proceso). Si el backend se
reinicia, las sesiones se pierden y hay que volver a loguearse. Para producción
multiproceso conviene un store compartido (Redis) — anotado como mejora futura.
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from .config import settings
from .koha.client import KohaAuthError, KohaClient
from .koha.reports import KohaRepository

logger = logging.getLogger("auth")

ALGORITHM = "HS256"
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

# sid -> KohaClient autenticado de la bibliotecaria.
SESSIONS: dict[str, KohaClient] = {}


async def authenticate(username: str, password: str) -> dict[str, str]:
    """Verifica credenciales contra Koha y devuelve un token de sesión."""
    client = KohaClient(settings.koha_base_url, username, password)
    try:
        await client.login()
    except KohaAuthError as exc:
        await client.aclose()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    sid = secrets.token_urlsafe(24)
    SESSIONS[sid] = client

    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.app_token_expire_minutes)
    token = jwt.encode(
        {"sub": username, "sid": sid, "exp": expire},
        settings.app_secret_key,
        algorithm=ALGORITHM,
    )
    logger.info("Login OK: %s (sid=%s…)", username, sid[:6])
    return {"access_token": token, "token_type": "bearer", "username": username}


async def logout(sid: str) -> None:
    client = SESSIONS.pop(sid, None)
    if client:
        await client.aclose()


def _decode(token: str) -> dict:
    try:
        return jwt.decode(token, settings.app_secret_key, algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido o expirado.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


async def get_current_client(token: str = Depends(oauth2_scheme)) -> KohaClient:
    """Resuelve el token -> KohaClient autenticado de la sesión."""
    payload = _decode(token)
    sid = payload.get("sid")
    client = SESSIONS.get(sid) if sid else None
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sesión no encontrada (el servidor se reinició o expiró). Volvé a entrar.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return client


async def get_repository(client: KohaClient = Depends(get_current_client)) -> KohaRepository:
    """Dependencia para los endpoints: repositorio ligado a la sesión actual."""
    return KohaRepository(client)


async def get_current_username(token: str = Depends(oauth2_scheme)) -> str:
    return _decode(token).get("sub", "")
