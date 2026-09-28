"""Usuarios propios de la app (comisión directiva y subcomisiones).

Las bibliotecarias entran con su usuario de Koha (ver `auth.py`) y no necesitan
figurar acá. Pero la comisión directiva y las subcomisiones no tienen usuario de
Koha, así que la app necesita sus propios usuarios: eso es este módulo.

Guardado: `storage` (archivo JSON en local, Postgres `app_kv` en producción) bajo
la clave `usuarios`. Las contraseñas se guardan **hasheadas** (PBKDF2-SHA256), nunca
en claro; ni siquiera quien administra puede verlas, solo resetearlas.

El primer usuario lo crea una bibliotecaria desde la app: como las bibliotecarias se
autentican contra Koha y siempre tienen permiso de administración, no hace falta
ningún usuario semilla ni script de arranque.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import threading
from datetime import datetime, timezone

from . import storage
from .permisos import ROLES

logger = logging.getLogger("usuarios")

CLAVE = "usuarios"
_LOCK = threading.Lock()          # serializa el leer-todo / escribir-todo

_ITERACIONES = 200_000
_ALGO = "pbkdf2_sha256"


# ── Contraseñas ─────────────────────────────────────────────────────────────
def hashear(password: str) -> str:
    """Devuelve 'pbkdf2_sha256$<iter>$<salt_hex>$<hash_hex>'."""
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERACIONES)
    return f"{_ALGO}${_ITERACIONES}${salt.hex()}${dk.hex()}"


def verificar_hash(password: str, guardado: str) -> bool:
    """Compara en tiempo constante. Tolera un hash vacío o con formato raro."""
    try:
        algo, iters, salt_hex, hash_hex = guardado.split("$")
        if algo != _ALGO:
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iters))
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(dk.hex(), hash_hex)


def password_aleatoria(largo: int = 10) -> str:
    """Contraseña provisoria legible (sin caracteres que se confunden: l/1/O/0)."""
    alfabeto = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alfabeto) for _ in range(largo))


# ── Acceso al almacenamiento ────────────────────────────────────────────────
def _leer() -> list[dict]:
    datos = storage.get(CLAVE)
    return datos if isinstance(datos, list) else []


def _guardar(lista: list[dict]) -> None:
    storage.set(CLAVE, lista)


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm(v: str | None) -> str:
    return (v or "").strip().lower()


def _publico(u: dict) -> dict:
    """El usuario tal como sale por la API: sin el hash de la contraseña."""
    return {k: v for k, v in u.items() if k != "password"}


# ── Consultas ───────────────────────────────────────────────────────────────
def listar(incluir_inactivos: bool = True) -> list[dict]:
    us = [_publico(u) for u in _leer()]
    if not incluir_inactivos:
        us = [u for u in us if u.get("activo")]
    return sorted(us, key=lambda u: (u.get("rol", ""), _norm(u.get("nombre"))))


def obtener(uid: str) -> dict | None:
    return next((_publico(u) for u in _leer() if u.get("id") == uid), None)


def _buscar_crudo(login: str) -> dict | None:
    """Busca por usuario o por email (sin distinguir mayúsculas). Devuelve el registro con hash."""
    login = _norm(login)
    if not login:
        return None
    for u in _leer():
        if _norm(u.get("usuario")) == login or _norm(u.get("email")) == login:
            return u
    return None


def existe(login: str) -> bool:
    return _buscar_crudo(login) is not None


# ── Alta / edición ──────────────────────────────────────────────────────────
class ErrorUsuario(ValueError):
    """Dato inválido al crear o editar un usuario (se traduce a HTTP 400)."""


def crear(*, usuario: str, nombre: str, rol: str, password: str | None = None,
          email: str = "", subcomision: str = "", creado_por: str = "") -> tuple[dict, str]:
    """Crea un usuario. Devuelve (usuario_público, contraseña_en_claro).

    La contraseña en claro se devuelve **una sola vez**, para poder dictársela a la
    persona. Después ya no se puede recuperar: solo resetear.
    """
    usuario, nombre, rol = (usuario or "").strip(), (nombre or "").strip(), (rol or "").strip()
    if not usuario:
        raise ErrorUsuario("Falta el nombre de usuario.")
    if not nombre:
        raise ErrorUsuario("Falta el nombre de la persona.")
    if rol not in ROLES:
        raise ErrorUsuario(f"Rol desconocido: '{rol}'. Válidos: {', '.join(ROLES)}.")
    if rol == "subcomision" and not subcomision.strip():
        raise ErrorUsuario("Un usuario de subcomisión necesita indicar cuál.")

    with _LOCK:
        lista = _leer()
        _exigir_libres(lista, usuario, email)
        en_claro = password or password_aleatoria()
        nuevo = {
            "id": secrets.token_hex(8),
            "usuario": usuario,
            "nombre": nombre,
            "email": email.strip(),
            "rol": rol,
            "subcomision": subcomision.strip(),
            "activo": True,
            "password": hashear(en_claro),
            "creado": _ahora(),
            "creado_por": creado_por,
            "actualizado": _ahora(),
            "ultimo_acceso": None,
        }
        lista.append(nuevo)
        _guardar(lista)
    logger.info("Usuario creado: %s (rol=%s) por %s", usuario, rol, creado_por or "?")
    return _publico(nuevo), en_claro


def _exigir_libres(lista: list[dict], *valores: str, excepto: str | None = None) -> None:
    """Se entra con el usuario o con el email, así que ninguno de los dos puede coincidir
    con el usuario ni con el email de otra persona: si no, uno de los dos no entra nunca."""
    for v in valores:
        if not _norm(v):
            continue
        for u in lista:
            if u.get("id") != excepto and _norm(v) in (_norm(u.get("usuario")), _norm(u.get("email"))):
                raise ErrorUsuario(f"Ya existe un usuario con '{v.strip()}'.")


_EDITABLES = {"nombre", "email", "rol", "subcomision", "activo"}


def actualizar(uid: str, cambios: dict, *, editado_por: str = "") -> dict:
    """Edita nombre, email, rol, subcomisión o activo. Ignora el resto."""
    cambios = {k: v for k, v in (cambios or {}).items() if k in _EDITABLES}
    if "rol" in cambios and cambios["rol"] not in ROLES:
        raise ErrorUsuario(f"Rol desconocido: '{cambios['rol']}'.")
    with _LOCK:
        lista = _leer()
        u = next((x for x in lista if x.get("id") == uid), None)
        if u is None:
            raise ErrorUsuario("El usuario no existe.")
        if "email" in cambios:
            _exigir_libres(lista, cambios["email"], excepto=uid)
        u.update(cambios)
        if u.get("rol") == "subcomision" and not (u.get("subcomision") or "").strip():
            raise ErrorUsuario("Un usuario de subcomisión necesita indicar cuál.")
        u["actualizado"] = _ahora()
        _guardar(lista)
    logger.info("Usuario editado: %s (%s) por %s", u.get("usuario"), ", ".join(cambios) or "sin cambios", editado_por or "?")
    return _publico(u)


def resetear_password(uid: str, *, nueva: str | None = None, editado_por: str = "") -> str:
    """Pone una contraseña nueva y la devuelve en claro (una sola vez)."""
    with _LOCK:
        lista = _leer()
        u = next((x for x in lista if x.get("id") == uid), None)
        if u is None:
            raise ErrorUsuario("El usuario no existe.")
        en_claro = nueva or password_aleatoria()
        u["password"] = hashear(en_claro)
        u["actualizado"] = _ahora()
        _guardar(lista)
    logger.info("Contraseña reseteada: %s por %s", u.get("usuario"), editado_por or "?")
    return en_claro


def cambiar_password(uid: str, actual: str, nueva: str) -> None:
    """Cambio hecho por la propia persona: exige la contraseña actual."""
    if len((nueva or "").strip()) < 8:
        raise ErrorUsuario("La contraseña nueva tiene que tener al menos 8 caracteres.")
    with _LOCK:
        lista = _leer()
        u = next((x for x in lista if x.get("id") == uid), None)
        if u is None:
            raise ErrorUsuario("El usuario no existe.")
        if not verificar_hash(actual, u.get("password", "")):
            raise ErrorUsuario("La contraseña actual no es correcta.")
        u["password"] = hashear(nueva)
        u["actualizado"] = _ahora()
        _guardar(lista)


def borrar(uid: str, *, editado_por: str = "") -> None:
    """Baja definitiva. En general conviene desactivar (activo=False) en vez de borrar."""
    with _LOCK:
        lista = _leer()
        quedan = [u for u in lista if u.get("id") != uid]
        if len(quedan) == len(lista):
            raise ErrorUsuario("El usuario no existe.")
        _guardar(quedan)
    logger.info("Usuario borrado: %s por %s", uid, editado_por or "?")


# ── Login ───────────────────────────────────────────────────────────────────
def verificar(login: str, password: str) -> dict | None:
    """Valida credenciales de un usuario propio de la app.

    Devuelve el usuario (sin hash) si están bien, o None. Un usuario desactivado
    nunca entra, aunque la contraseña sea correcta.
    """
    u = _buscar_crudo(login)
    if u is None:
        # Igual gastamos el tiempo del hash: así no se puede deducir qué usuarios
        # existen midiendo cuánto tarda la respuesta.
        hashlib.pbkdf2_hmac("sha256", (password or "").encode(), b"x" * 16, _ITERACIONES)
        return None
    if not verificar_hash(password or "", u.get("password", "")):
        return None
    if not u.get("activo"):
        logger.info("Login rechazado (usuario desactivado): %s", u.get("usuario"))
        return None
    return _publico(u)


def registrar_acceso(uid: str) -> None:
    """Deja constancia del último ingreso. Si falla, no rompe el login."""
    try:
        with _LOCK:
            lista = _leer()
            u = next((x for x in lista if x.get("id") == uid), None)
            if u:
                u["ultimo_acceso"] = _ahora()
                _guardar(lista)
    except Exception as exc:  # noqa: BLE001
        logger.warning("No se pudo registrar el acceso de %s: %s", uid, exc)


# ── Subcomisiones ───────────────────────────────────────────────────────────
def subcomisiones() -> list[str]:
    """Lista de subcomisiones: las de la variable SUBCOMISIONES más las ya usadas."""
    de_env = [s.strip() for s in os.getenv("SUBCOMISIONES", "").split(",") if s.strip()]
    en_uso = {(u.get("subcomision") or "").strip() for u in _leer()}
    return sorted({*de_env, *(s for s in en_uso if s)})
