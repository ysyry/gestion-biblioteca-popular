"""Endpoints de la API. Todos los de datos requieren sesión (token Bearer)."""
from __future__ import annotations

import asyncio
import logging
from collections import Counter
from datetime import date, timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import Response

from ..auth import (
    Sesion,
    authenticate,
    get_current_username,
    get_repository,
    get_session,
    logout,
    oauth2_scheme,
    requiere,
    _decode,
)
from .. import mail
from .. import auto_mail
from .. import historial
from .. import tracking
from .. import agenda
from .. import cuotas
from .. import pagos
from .. import cache
from .. import espacios
from .. import notas
from .. import permisos
from .. import pizarron
from .. import registro
from .. import solicitudes
from .. import usuarios
from ..config import settings

# TTL (segundos) por tipo de dato. Lo que cambia rápido, caché corto.
TTL_LOANS = 120        # préstamos vigentes (cambian durante el día)
TTL_CATALOG = 3600     # catálogo (cambia día a día)
TTL_HEAVY = 1800       # estrategia / histórico (datos históricos)
TTL_CRUCE = 900        # cruce Koha↔planilla
TTL_AGENDA = 1800      # agenda (eventos cambian lento)
TTL_NOTAS = 60         # notas de socios: se cargan en Koha durante el día
TTL_AVISOS = 300       # 📝 en Préstamos / Mails: un vistazo, no hace falta al segundo
AVISOS_DIAS = 60       # una novedad sin resolver "reciente" es de los últimos 60 días


async def _loans_contact_cached(repo: KohaRepository, fresh: bool = False):
    """Préstamos vigentes con caché compartido (lo usan préstamos, stats y cruce)."""
    if fresh:
        cache.invalidate("loans_contact")
    return await cache.cached("loans_contact", TTL_LOANS, repo.loans_contact, swr=not fresh)
from ..koha.client import KohaClient, KohaError
from ..koha.reports import KohaRepository
from ..schemas import LoginRequest, LoginResponse, MailSendRequest

logger = logging.getLogger("api")
router = APIRouter(prefix="/api")


# ── Auth ───────────────────────────────────────────────────────────────────
@router.post("/auth/login", response_model=LoginResponse, tags=["auth"])
async def login(body: LoginRequest):
    """Inicia sesión con las credenciales de Koha de la bibliotecaria."""
    return await authenticate(body.username, body.password)


@router.post("/auth/logout", tags=["auth"])
async def do_logout(token: str = Depends(oauth2_scheme)):
    sid = _decode(token).get("sid")
    if sid:
        await logout(sid)
    return {"ok": True}


@router.get("/me", tags=["auth"])
async def me(s: Sesion = Depends(get_session)):
    """Quién está adentro y qué puede ver. El frontend arma el menú con esto."""
    return {
        "username": s.usuario,
        "nombre": s.nombre,
        "rol": s.rol,
        "rol_etiqueta": permisos.ETIQUETAS.get(s.rol, s.rol),
        "subcomision": s.subcomision,
        "es_de_koha": s.es_de_koha,
        "permisos": sorted(s.permisos),
        "secciones": permisos.secciones_de(s.rol),
        "menu": permisos.menu_de(s.rol),
    }


# ── Usuarios de la app (comisión directiva y subcomisiones) ──────────────────
@router.get("/usuarios", tags=["usuarios"])
async def usuarios_listar(s: Sesion = Depends(requiere(permisos.USUARIOS_ADMIN))):
    """Usuarios propios de la app. Las bibliotecarias no están acá: entran con Koha."""
    return {
        "items": usuarios.listar(),
        "roles": [{"id": r, "titulo": permisos.ETIQUETAS.get(r, r)} for r in permisos.ROLES],
        "subcomisiones": usuarios.subcomisiones(),
    }


@router.post("/usuarios", tags=["usuarios"])
async def usuarios_crear(body: dict = Body(...),
                         s: Sesion = Depends(requiere(permisos.USUARIOS_ADMIN))):
    """Crea un usuario. Devuelve la contraseña en claro UNA sola vez, para dictarla."""
    b = body or {}
    try:
        u, clave = usuarios.crear(
            usuario=b.get("usuario", ""),
            nombre=b.get("nombre", ""),
            rol=b.get("rol", ""),
            password=(b.get("password") or "").strip() or None,
            email=b.get("email", ""),
            subcomision=b.get("subcomision", ""),
            creado_por=s.usuario,
        )
    except usuarios.ErrorUsuario as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"usuario": u, "password": clave}


@router.put("/usuarios/{uid}", tags=["usuarios"])
async def usuarios_actualizar(uid: str, body: dict = Body(...),
                              s: Sesion = Depends(requiere(permisos.USUARIOS_ADMIN))):
    """Edita nombre, email, rol, subcomisión o si está activo."""
    if uid == s.uid and body.get("activo") is False:
        raise HTTPException(status_code=400, detail="No podés desactivarte a vos misma/o.")
    try:
        return usuarios.actualizar(uid, body or {}, editado_por=s.usuario)
    except usuarios.ErrorUsuario as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/usuarios/{uid}/password", tags=["usuarios"])
async def usuarios_resetear(uid: str, body: dict = Body(default={}),
                            s: Sesion = Depends(requiere(permisos.USUARIOS_ADMIN))):
    """Resetea la contraseña. Devuelve la nueva en claro, una sola vez."""
    try:
        clave = usuarios.resetear_password(
            uid, nueva=(body or {}).get("password") or None, editado_por=s.usuario)
    except usuarios.ErrorUsuario as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"password": clave}


@router.delete("/usuarios/{uid}", tags=["usuarios"])
async def usuarios_borrar(uid: str, s: Sesion = Depends(requiere(permisos.USUARIOS_ADMIN))):
    """Baja definitiva. En general conviene desactivar en vez de borrar."""
    if uid == s.uid:
        raise HTTPException(status_code=400, detail="No podés borrarte a vos misma/o.")
    try:
        usuarios.borrar(uid, editado_por=s.usuario)
    except usuarios.ErrorUsuario as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/password", tags=["usuarios"])
async def password_propia(body: dict = Body(...), s: Sesion = Depends(get_session)):
    """Cambio de contraseña de la propia persona (solo usuarios de la app)."""
    if not s.uid:
        raise HTTPException(
            status_code=400,
            detail="Tu contraseña es la de Koha: se cambia desde Koha, no desde acá.")
    try:
        usuarios.cambiar_password(s.uid, (body or {}).get("actual", ""),
                                  (body or {}).get("nueva", ""))
    except usuarios.ErrorUsuario as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


# ── Registro de actividades realizadas ────────────────────────────────────────
# La parte pública (el formulario que se comparte por link) no pide usuario: está
# más abajo, en "Formulario público". Esta parte es la de la biblioteca.
def _registro_error(exc: registro.ErrorRegistro) -> HTTPException:
    if isinstance(exc, registro.NoEncontrado):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, registro.LinkCerrado):
        return HTTPException(status_code=410, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


def _link_publico(link: dict) -> dict:
    """El link con su dirección armada, para copiar y compartir."""
    base = (settings.app_public_url or "").rstrip("/")
    return {**link, "url": f"{base}/registro/{link['token']}" if base else f"/registro/{link['token']}"}


@router.get("/registro", tags=["registro"])
async def registro_listar(estado: str | None = Query(None, pattern="^(recibido|validado|descartado)$"),
                          clase: str | None = Query(None, pattern="^(actividad|taller)$"),
                          desde: str | None = Query(None), hasta: str | None = Query(None),
                          anio: int | None = Query(None, ge=2000, le=2100),
                          _: Sesion = Depends(requiere(permisos.REGISTROS))):
    """Bandeja de registros. Sin filtro de estado vienen todos, recibidos primero."""
    items = [registro.para_ver(r) for r in
             registro.listar(estado=estado, clase=clase, desde=desde, hasta=hasta, anio=anio)]
    if not estado:
        items.sort(key=lambda r: (r["estado"] != registro.RECIBIDO, ))
    return {"items": items, "pendientes": registro.pendientes(),
            "atencion": [registro.para_ver(r) for r in registro.con_atencion()],
            "catalogos": registro.catalogos(), "estados": registro.ESTADOS}


@router.get("/registro/links", tags=["registro"])
async def registro_links(_: Sesion = Depends(requiere(permisos.REGISTROS))):
    return {"items": [_link_publico(l) for l in registro.links()],
            "catalogos": registro.catalogos(),
            "publico_configurado": bool(settings.app_public_url)}


@router.post("/registro/links", tags=["registro"])
async def registro_link_crear(body: dict = Body(...), s: Sesion = Depends(requiere(permisos.REGISTROS))):
    """Crea un link: general, de una actividad (precargado) o de un taller (mensual)."""
    try:
        return _link_publico(registro.crear_link(body or {}, s.nombre or s.usuario))
    except registro.ErrorRegistro as exc:
        raise _registro_error(exc) from exc


@router.put("/registro/links/{link_id}", tags=["registro"])
async def registro_link_editar(link_id: str, body: dict = Body(...),
                               _: Sesion = Depends(requiere(permisos.REGISTROS))):
    """Abrir o cerrar un link, cambiarle el nombre o la fecha de vencimiento."""
    try:
        return _link_publico(registro.actualizar_link(link_id, body or {}))
    except registro.ErrorRegistro as exc:
        raise _registro_error(exc) from exc


@router.delete("/registro/links/{link_id}", tags=["registro"])
async def registro_link_borrar(link_id: str, _: Sesion = Depends(requiere(permisos.REGISTROS))):
    try:
        registro.borrar_link(link_id)
    except registro.ErrorRegistro as exc:
        raise _registro_error(exc) from exc
    return {"ok": True}


@router.get("/registro/export.csv", tags=["registro"])
async def registro_export(estado: str | None = Query("validado", pattern="^(recibido|validado|descartado|todos)$"),
                          anio: int | None = Query(None, ge=2000, le=2100),
                          _: Sesion = Depends(requiere(permisos.REGISTROS))):
    """Todos los registros en CSV, para abrir en una planilla."""
    items = registro.listar(estado=None if estado == "todos" else estado, anio=anio)
    csv_texto = registro.exportar_csv(sorted(items, key=registro.cuando_de))
    nombre = f"actividades-{anio or 'todo'}-{estado}.csv"
    return Response(content="﻿" + csv_texto, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


@router.get("/registro/{reg_id}", tags=["registro"])
async def registro_detalle(reg_id: str, _: Sesion = Depends(requiere(permisos.REGISTROS))):
    try:
        return registro.para_ver(registro.obtener(reg_id))
    except registro.ErrorRegistro as exc:
        raise _registro_error(exc) from exc


@router.post("/registro", tags=["registro"])
async def registro_cargar(body: dict = Body(...), s: Sesion = Depends(requiere(permisos.REGISTROS))):
    """Carga directa desde la app (queda validado: lo está cargando la biblioteca)."""
    try:
        return registro.guardar((body or {}).get("clase", "actividad"), (body or {}).get("datos") or {},
                                cargado_por=s.nombre or s.usuario)
    except registro.ErrorRegistro as exc:
        raise _registro_error(exc) from exc


@router.put("/registro/{reg_id}", tags=["registro"])
async def registro_corregir(reg_id: str, body: dict = Body(...),
                            s: Sesion = Depends(requiere(permisos.REGISTROS))):
    """Corrige o completa un registro. Queda constancia de qué se cambió."""
    try:
        return registro.para_ver(registro.corregir(reg_id, (body or {}).get("datos") or body or {},
                                                   s.nombre or s.usuario))
    except registro.ErrorRegistro as exc:
        raise _registro_error(exc) from exc


@router.post("/registro/{reg_id}/resolver", tags=["registro"])
async def registro_resolver(reg_id: str, body: dict = Body(...),
                            s: Sesion = Depends(requiere(permisos.REGISTROS))):
    """Validar o descartar. Solo lo validado cuenta en las estadísticas."""
    b = body or {}
    try:
        return registro.para_ver(registro.resolver(reg_id, b.get("estado", ""), s.nombre or s.usuario,
                                                   b.get("motivo", "")))
    except registro.ErrorRegistro as exc:
        raise _registro_error(exc) from exc


@router.delete("/registro/{reg_id}", tags=["registro"])
async def registro_borrar(reg_id: str, _: Sesion = Depends(requiere(permisos.REGISTROS))):
    try:
        registro.borrar(reg_id)
    except registro.ErrorRegistro as exc:
        raise _registro_error(exc) from exc
    return {"ok": True}


# ── Formulario público de registro (sin usuario) ──────────────────────────────
# Único lugar de la API que no pide sesión: entra cualquiera que tenga el link.
# Defensas: el código del link es largo e imposible de adivinar, se puede cerrar,
# hay un tope de envíos por conexión y la página nunca muestra datos guardados.
_ENVIOS_POR_IP: dict[str, list[float]] = {}
TOPE_ENVIOS = 12          # por hora y por conexión


def _permitir_envio(ip: str) -> bool:
    import time
    ahora = time.time()
    recientes = [t for t in _ENVIOS_POR_IP.get(ip, []) if ahora - t < 3600]
    if len(recientes) >= TOPE_ENVIOS:
        _ENVIOS_POR_IP[ip] = recientes
        return False
    recientes.append(ahora)
    _ENVIOS_POR_IP[ip] = recientes
    if len(_ENVIOS_POR_IP) > 5000:                      # no crecer sin fin
        for k in [k for k, v in _ENVIOS_POR_IP.items() if not v or ahora - v[-1] > 3600]:
            _ENVIOS_POR_IP.pop(k, None)
    return True


@router.get("/publico/registro/{token}", tags=["publico"])
async def publico_registro_form(token: str):
    """Qué formulario mostrar y con qué datos ya cargados. No requiere sesión."""
    try:
        link = registro.por_token(token)
    except registro.ErrorRegistro as exc:
        raise _registro_error(exc) from exc
    return {
        "clase": "taller" if link["clase"] == "taller" else "actividad",
        "titulo": link["titulo"],
        "precarga": link.get("precarga") or {},
        "catalogos": registro.catalogos(),
    }


@router.post("/publico/registro/{token}", tags=["publico"])
async def publico_registro_enviar(token: str, request: Request, body: dict = Body(...)):
    """Recibe el formulario. Queda 'recibido' hasta que la biblioteca lo valide."""
    b = body or {}
    if (b.get("website") or "").strip():                # campo trampa: solo lo completa un robot
        logger.info("Registro público: envío descartado por campo trampa.")
        return {"ok": True}
    ip = (request.client.host if request.client else "") or "desconocida"
    if not _permitir_envio(ip):
        raise HTTPException(status_code=429,
                            detail="Se enviaron muchos formularios seguidos. Probá de nuevo en un rato.")
    try:
        link = registro.por_token(token)
        clase = "taller" if link["clase"] == "taller" else "actividad"
        reg = registro.guardar(clase, {**(link.get("precarga") or {}), **(b.get("datos") or {})}, link=link)
    except registro.ErrorRegistro as exc:
        raise _registro_error(exc) from exc
    if link["clase"] == "actividad":                    # el de una actividad puntual se cierra solo
        registro.actualizar_link(link["id"], {"abierto": False})
    return {"ok": True, "id": reg["id"],
            "resumen": {"titulo": reg["datos"]["titulo"],
                        "cuando": registro.cuando_de(reg),
                        "personas": reg["datos"].get("personas_total") or reg["datos"].get("participantes")}}


# ── Pizarrón semanal (solo bibliotecarias) ────────────────────────────────────
def _pizarron_error(exc: pizarron.ErrorPizarron) -> HTTPException:
    if isinstance(exc, pizarron.SinPermiso):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, pizarron.NoEncontrada):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/pizarron", tags=["pizarron"])
async def pizarron_semana(semana: str | None = Query(None, description="Cualquier día de la semana (AAAA-MM-DD)"),
                          s: Sesion = Depends(requiere(permisos.PIZARRON))):
    """La semana del pizarrón: fijadas, notas generales, por día y tareas hechas."""
    try:
        d = pizarron.semana(semana, s.usuario, s.nombre)
    except pizarron.ErrorPizarron as exc:
        raise _pizarron_error(exc) from exc
    pizarron.registrar_visita(s.usuario, s.nombre)
    return d


@router.get("/pizarron/novedades", tags=["pizarron"])
async def pizarron_novedades(s: Sesion = Depends(requiere(permisos.PIZARRON))):
    """Cuántas notas y respuestas de otras dejaron desde la última visita (para el menú)."""
    return {"nuevas": pizarron.novedades(s.usuario)}


@router.get("/pizarron/buscar", tags=["pizarron"])
async def pizarron_buscar(q: str = Query(..., min_length=1, max_length=100),
                          s: Sesion = Depends(requiere(permisos.PIZARRON))):
    return {"items": pizarron.buscar(q, s.usuario, s.nombre)}


@router.post("/pizarron", tags=["pizarron"])
async def pizarron_crear(body: dict = Body(...), s: Sesion = Depends(requiere(permisos.PIZARRON))):
    try:
        return pizarron.crear(body or {}, s.usuario, s.nombre)
    except pizarron.ErrorPizarron as exc:
        raise _pizarron_error(exc) from exc


@router.put("/pizarron/{nota_id}", tags=["pizarron"])
async def pizarron_editar(nota_id: str, body: dict = Body(...),
                          s: Sesion = Depends(requiere(permisos.PIZARRON))):
    """Editar: solo quien la escribió."""
    try:
        return pizarron.editar(nota_id, body or {}, s.usuario)
    except pizarron.ErrorPizarron as exc:
        raise _pizarron_error(exc) from exc


@router.delete("/pizarron/{nota_id}", tags=["pizarron"])
async def pizarron_borrar(nota_id: str, s: Sesion = Depends(requiere(permisos.PIZARRON))):
    """Borrar: solo quien la escribió."""
    try:
        pizarron.borrar(nota_id, s.usuario)
    except pizarron.ErrorPizarron as exc:
        raise _pizarron_error(exc) from exc
    return {"ok": True}


@router.post("/pizarron/{nota_id}/hecha", tags=["pizarron"])
async def pizarron_hecha(nota_id: str, body: dict = Body(default={}),
                         s: Sesion = Depends(requiere(permisos.PIZARRON))):
    """Tildar (o destildar con {"hecha": false}) una tarea. Cualquiera del equipo."""
    try:
        return pizarron.marcar_hecha(nota_id, bool((body or {}).get("hecha", True)), s.usuario, s.nombre)
    except pizarron.ErrorPizarron as exc:
        raise _pizarron_error(exc) from exc


@router.post("/pizarron/{nota_id}/respuestas", tags=["pizarron"])
async def pizarron_responder(nota_id: str, body: dict = Body(...),
                             s: Sesion = Depends(requiere(permisos.PIZARRON))):
    try:
        return pizarron.responder(nota_id, (body or {}).get("texto", ""), s.usuario, s.nombre)
    except pizarron.ErrorPizarron as exc:
        raise _pizarron_error(exc) from exc


@router.delete("/pizarron/{nota_id}/respuestas/{respuesta_id}", tags=["pizarron"])
async def pizarron_borrar_respuesta(nota_id: str, respuesta_id: str,
                                    s: Sesion = Depends(requiere(permisos.PIZARRON))):
    try:
        return pizarron.borrar_respuesta(nota_id, respuesta_id, s.usuario)
    except pizarron.ErrorPizarron as exc:
        raise _pizarron_error(exc) from exc


# ── Préstamos ────────────────────────────────────────────────────────────────
@router.get("/loans/active", tags=["loans"])
async def loans_active(repo: KohaRepository = Depends(get_repository)):
    """Préstamos vigentes (todo lo que está prestado ahora)."""
    return await repo.active_loans()


@router.get("/loans/overdue", tags=["loans"])
async def loans_overdue(repo: KohaRepository = Depends(get_repository)):
    """Préstamos vencidos, con días de atraso y contacto del socio."""
    return await repo.overdue_loans()


@router.get("/loans/contact", tags=["loans"])
async def loans_contact(fresh: bool = Query(False), repo: KohaRepository = Depends(get_repository)):
    """Todos los préstamos vigentes con contacto y días respecto del vencimiento.

    dias_atraso > 0 → vencido; = 0 → vence hoy; < 0 → activo (faltan N días para vencer).
    """
    return await _loans_contact_cached(repo, fresh)


# ── Estadísticas ────────────────────────────────────────────────────────────
def _consultor(repo: KohaRepository, panel: str):
    """Arma la función de consulta de un panel y la lista donde anota los fallos.

    Antes cada panel se tragaba los errores y devolvía [], que aguas abajo se
    convertía en 0. Resultado: un tablero lleno de ceros que parecía un dato real
    ("la biblioteca tiene 0 socios") cuando en verdad Koha no había contestado.
    Ahora el fallo se anota y el panel decide: si lo que falló son los números
    principales, corta con error; si es un gráfico suelto, avisa y sigue.
    """
    fallos: list[str] = []

    async def q(sql):
        try:
            return await repo.run_sql(sql)
        except Exception as exc:  # noqa: BLE001
            logger.warning("stats/%s: consulta falló: %s", panel, exc)
            fallos.append(str(exc))
            return None          # None = no se pudo leer (distinto de "no hay datos")

    return q, fallos


def _exigir(*resultados) -> None:
    """Corta con 502 si alguna consulta principal no se pudo leer."""
    if any(r is None for r in resultados):
        raise HTTPException(
            status_code=502,
            detail="No se pudieron leer los datos de Koha. Probá de nuevo en un momento; "
                   "si sigue igual, avisá que Koha no está respondiendo.",
        )


def _dias_int(r) -> int | None:
    try:
        return int(r.get("dias_atraso"))
    except (TypeError, ValueError):
        return None


@router.get("/stats", tags=["stats"])
async def stats(fresh: bool = Query(False), repo: KohaRepository = Depends(get_repository)):
    """KPIs calculados sobre los préstamos vigentes (reporte loans_contact)."""
    rows = await _loans_contact_cached(repo, fresh)
    total = len(rows)
    vencidos = [r for r in rows if (_dias_int(r) or 0) > 0]
    por_vencer = [r for r in rows if _dias_int(r) is not None and _dias_int(r) <= 0]
    socios = {r.get("cardnumber") for r in rows if r.get("cardnumber")}
    socios_venc = {r.get("cardnumber") for r in vencidos if r.get("cardnumber")}

    buckets = {"por_vencer": 0, "venc_1_30": 0, "venc_31_90": 0, "venc_90": 0}
    for r in rows:
        d = _dias_int(r)
        if d is None:
            continue
        if d <= 0:
            buckets["por_vencer"] += 1
        elif d <= 30:
            buckets["venc_1_30"] += 1
        elif d <= 90:
            buckets["venc_31_90"] += 1
        else:
            buckets["venc_90"] += 1

    titulos = Counter((r.get("title") or "(sin título)") for r in rows)
    top_titulos = [{"label": t, "count": c} for t, c in titulos.most_common(10)]

    por_socio: Counter = Counter()
    nombres: dict = {}
    for r in rows:
        c = r.get("cardnumber")
        if not c:
            continue
        por_socio[c] += 1
        nombres[c] = (f'{r.get("surname", "")}, {r.get("firstname", "")}').strip(", ")
    top_socios = [{"label": nombres.get(c, c), "count": n} for c, n in por_socio.most_common(10)]

    return {
        "total_vigentes": total,
        "vencidos": len(vencidos),
        "por_vencer": len(por_vencer),
        "pct_vencidos": round(100 * len(vencidos) / total, 1) if total else 0,
        "socios_con_prestamos": len(socios),
        "socios_con_vencidos": len(socios_venc),
        "buckets": buckets,
        "top_titulos": top_titulos,
        "top_socios": top_socios,
    }


_SQL_TOTALES = """
SELECT
  (SELECT COUNT(*) FROM items) AS ejemplares,
  (SELECT COUNT(DISTINCT biblionumber) FROM items) AS titulos,
  (SELECT COUNT(*) FROM items WHERE issues IS NULL OR issues = 0) AS sin_circular,
  (SELECT COUNT(*) FROM items WHERE itemlost > 0) AS perdidos,
  (SELECT COUNT(*) FROM items WHERE damaged > 0) AS danados,
  (SELECT COUNT(*) FROM items WHERE withdrawn > 0) AS retirados
""".strip()

_SQL_TIPOS = """
SELECT COALESCE(t.description, NULLIF(i.itype,''), '(sin tipo)') AS label, COUNT(*) AS count
FROM items i LEFT JOIN itemtypes t ON t.itemtype = i.itype
GROUP BY label ORDER BY count DESC LIMIT 12
""".strip()

_SQL_TOP_HIST = """
SELECT b.title AS label, i.issues AS count
FROM items i JOIN biblio b ON b.biblionumber = i.biblionumber
WHERE i.issues > 0 ORDER BY i.issues DESC LIMIT 10
""".strip()


def _num(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


@router.get("/stats/catalog", tags=["stats"])
async def stats_catalog(fresh: bool = Query(False), repo: KohaRepository = Depends(get_repository)):
    """KPIs del catálogo (ejemplares, títulos, circulación, tipos, colecciones)."""
    if fresh:
        cache.invalidate("stats_catalog")
    return await cache.cached("stats_catalog", TTL_CATALOG, lambda: _stats_catalog(repo), swr=not fresh)


async def _stats_catalog(repo: KohaRepository):
    q, fallos = _consultor(repo, "catalog")

    totals, tipos, top = await asyncio.gather(q(_SQL_TOTALES), q(_SQL_TIPOS), q(_SQL_TOP_HIST))
    _exigir(totals)                       # sin los totales el panel no dice nada cierto
    tipos, top = tipos or [], top or []
    t = totals[0] if totals else {}
    ejemplares, titulos = _num(t.get("ejemplares")), _num(t.get("titulos"))
    sin_circular = _num(t.get("sin_circular"))

    def serie(rows):
        return [{"label": r.get("label") or "—", "count": _num(r.get("count"))} for r in rows]

    return {
        "ejemplares": ejemplares,
        "titulos": titulos,
        "sin_circular": sin_circular,
        "pct_sin_circular": round(100 * sin_circular / ejemplares, 1) if ejemplares else 0,
        "perdidos": _num(t.get("perdidos")),
        "danados": _num(t.get("danados")),
        "retirados": _num(t.get("retirados")),
        "por_tipo": serie(tipos),
        "top_historico": serie(top),
        "avisos": fallos,
    }


@router.get("/stats/historico", tags=["stats"])
async def stats_historico(
    desde: str | None = Query(None, description="YYYY-MM-DD"),
    hasta: str | None = Query(None, description="YYYY-MM-DD"),
    fresh: bool = Query(False),
    repo: KohaRepository = Depends(get_repository),
):
    """Estadísticas de circulación (tabla statistics) en un rango de fechas."""
    import datetime as dt
    today = dt.date.today()
    try:
        d2 = dt.date.fromisoformat(hasta) if hasta else today
        d1 = dt.date.fromisoformat(desde) if desde else (d2 - dt.timedelta(days=365))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Fechas inválidas (YYYY-MM-DD).") from exc
    if d1 > d2:
        d1, d2 = d2, d1
    key = f"hist:{d1.isoformat()}:{d2.isoformat()}"
    if fresh:
        cache.invalidate(key)
    return await cache.cached(key, TTL_HEAVY, lambda: _stats_historico(repo, d1, d2), swr=not fresh)


async def _stats_historico(repo: KohaRepository, d1, d2):
    # Fechas re-serializadas desde objetos date -> seguras para interpolar.
    rango = f"s.datetime >= '{d1.isoformat()} 00:00:00' AND s.datetime <= '{d2.isoformat()} 23:59:59'"

    q, fallos = _consultor(repo, "historico")

    totales, por_mes, top_titulos, top_socios = await asyncio.gather(
        q(f"""
        SELECT SUM(s.type='issue') AS prestamos, SUM(s.type='return') AS devoluciones,
               SUM(s.type='renew') AS renovaciones,
               COUNT(DISTINCT CASE WHEN s.type='issue' THEN s.borrowernumber END) AS socios_activos
        FROM statistics s WHERE {rango}"""),
        q(f"""
        SELECT DATE_FORMAT(s.datetime,'%Y-%m') AS label, COUNT(*) AS count
        FROM statistics s WHERE s.type='issue' AND {rango}
        GROUP BY label ORDER BY label"""),
        q(f"""
        SELECT b.title AS label, COUNT(*) AS count
        FROM statistics s JOIN items i ON i.itemnumber=s.itemnumber
        JOIN biblio b ON b.biblionumber=i.biblionumber
        WHERE s.type='issue' AND {rango}
        GROUP BY b.title ORDER BY count DESC LIMIT 10"""),
        q(f"""
        SELECT CONCAT(br.surname, ', ', br.firstname) AS label, COUNT(*) AS count
        FROM statistics s JOIN borrowers br ON br.borrowernumber=s.borrowernumber
        WHERE s.type='issue' AND {rango}
        GROUP BY br.borrowernumber ORDER BY count DESC LIMIT 10"""),
    )

    _exigir(totales)                      # sin totales, los números serían inventados
    t = totales[0] if totales else {}

    def serie(rows):
        return [{"label": r.get("label") or "—", "count": _num(r.get("count"))}
                for r in (rows or [])]

    return {
        "desde": d1.isoformat(), "hasta": d2.isoformat(),
        "prestamos": _num(t.get("prestamos")),
        "devoluciones": _num(t.get("devoluciones")),
        "renovaciones": _num(t.get("renovaciones")),
        "socios_activos": _num(t.get("socios_activos")),
        "por_mes": serie(por_mes),
        "top_titulos": serie(top_titulos),
        "top_socios": serie(top_socios),
        "avisos": fallos,
    }


@router.get("/stats/estrategia", tags=["stats"])
async def stats_estrategia(fresh: bool = Query(False), repo: KohaRepository = Depends(get_repository)):
    """Panel estratégico: crecimiento, socios, estacionalidad y antigüedad del acervo."""
    if fresh:
        cache.invalidate("estrategia")
    return await cache.cached("estrategia", TTL_HEAVY, lambda: _stats_estrategia(repo), swr=not fresh)


async def _stats_estrategia(repo: KohaRepository):
    q, fallos = _consultor(repo, "estrategia")

    def serie(rows):
        return [{"label": str(r.get("label") or "—"), "count": _num(r.get("count"))}
                for r in (rows or [])]

    (prestamos_anio, socios_activos_anio, socios_nuevos_anio, estacionalidad,
     acervo_anio, socios_kpi, acervo_kpi) = await asyncio.gather(
        q("""SELECT YEAR(datetime) AS label, COUNT(*) AS count
        FROM statistics WHERE type='issue' AND datetime>='2013-01-01' GROUP BY label ORDER BY label"""),
        q("""SELECT YEAR(datetime) AS label, COUNT(DISTINCT borrowernumber) AS count
        FROM statistics WHERE type='issue' AND datetime>='2013-01-01' GROUP BY label ORDER BY label"""),
        q("""SELECT YEAR(dateenrolled) AS label, COUNT(*) AS count
        FROM borrowers WHERE dateenrolled IS NOT NULL GROUP BY label ORDER BY label"""),
        q("""SELECT MONTH(datetime) AS label, COUNT(*) AS count
        FROM statistics WHERE type='issue' GROUP BY label ORDER BY label"""),
        q("""SELECT YEAR(dateaccessioned) AS label, COUNT(*) AS count
        FROM items WHERE dateaccessioned IS NOT NULL AND YEAR(dateaccessioned) >= YEAR(CURDATE())-15
        GROUP BY label ORDER BY label"""),
        q("""SELECT
        (SELECT COUNT(*) FROM borrowers) AS total,
        (SELECT COUNT(*) FROM borrowers b WHERE EXISTS (
            SELECT 1 FROM statistics s WHERE s.borrowernumber=b.borrowernumber
            AND s.type='issue' AND s.datetime >= NOW() - INTERVAL 1 YEAR)) AS activos12,
        (SELECT COUNT(*) FROM borrowers b WHERE NOT EXISTS (
            SELECT 1 FROM statistics s WHERE s.borrowernumber=b.borrowernumber AND s.type='issue')) AS nunca"""),
        q("""SELECT
        (SELECT COUNT(*) FROM items) AS total_items,
        SUM(dateaccessioned >= CURDATE() - INTERVAL 1 YEAR) AS nuevos12,
        SUM(dateaccessioned >= CURDATE() - INTERVAL 5 YEAR) AS ult5
        FROM items"""),
    )

    _exigir(socios_kpi, acervo_kpi)       # mostrar 0 socios sería mentir
    sk = socios_kpi[0] if socios_kpi else {}
    ak = acervo_kpi[0] if acervo_kpi else {}
    total, activos12, nunca = _num(sk.get("total")), _num(sk.get("activos12")), _num(sk.get("nunca"))
    total_items, ult5 = _num(ak.get("total_items")), _num(ak.get("ult5"))

    return {
        "prestamos_por_anio": serie(prestamos_anio),
        "socios_activos_por_anio": serie(socios_activos_anio),
        "socios_nuevos_por_anio": serie(socios_nuevos_anio),
        "estacionalidad": serie(estacionalidad),
        "acervo_por_anio": serie(acervo_anio),
        "socios": {"total": total, "activos_12m": activos12,
                   "dormidos": max(total - activos12 - nunca, 0), "nunca": nunca},
        "acervo": {"nuevos_12m": _num(ak.get("nuevos12")), "ult_5": ult5,
                   "mas_5": max(total_items - ult5, 0)},
        # Consultas sueltas que fallaron: el panel se muestra igual, pero avisando
        # cuál gráfico quedó incompleto en vez de dibujarlo vacío como si fuera cero.
        "avisos": fallos,
    }


# ── Socios ────────────────────────────────────────────────────────────────────
@router.get("/members", tags=["members"])
async def members_search(
    q: str = Query(..., min_length=1, description="Apellido, nombre o número de carnet"),
    repo: KohaRepository = Depends(get_repository),
):
    """Busca socios por apellido/nombre/carnet."""
    return await repo.search_members(q)


@router.get("/members/{cardnumber}/loans", tags=["members"])
async def member_loans(cardnumber: str, repo: KohaRepository = Depends(get_repository)):
    """Préstamos vigentes de un socio puntual."""
    return await repo.member_loans(cardnumber)


@router.get("/members/{cardnumber}/profile", tags=["members"])
async def member_profile(cardnumber: str, repo: KohaRepository = Depends(get_repository)):
    """Ficha del socio: datos, préstamos vigentes e historial.

    Nota: lo relativo a pagos de cuotas (deuda / estado de cuenta) se gestiona en
    una planilla aparte; será un módulo separado (ver docs/ISSUE-modulo-pagos.md).
    """
    profile, loans, history, (notas_socio, notas_error) = await asyncio.gather(
        repo.member_profile(cardnumber),
        repo.member_loans(cardnumber),
        repo.member_history(cardnumber),
        _notas_de_socio(repo, cardnumber),
    )
    socio = profile[0] if profile else None
    if socio is None:
        raise HTTPException(status_code=404, detail="Socio no encontrado.")

    return {
        "socio": socio,
        "prestamos_vigentes": loans,
        "historial": history,
        "notas": notas_socio,
        "notas_error": notas_error,
    }


async def _notas_de_socio(repo: KohaRepository, cardnumber: str):
    """Notas del socio para la ficha. Si esa consulta falla, la ficha se muestra igual:
    devuelve (None, motivo) y la pantalla avisa, en vez de dibujar "sin notas"."""
    try:
        return notas.armar(await repo.member_notes(cardnumber)), None
    except Exception as exc:  # noqa: BLE001
        logger.warning("notas del socio %s: %s", cardnumber, exc)
        return None, "No se pudieron leer las notas desde Koha."


# ── Notas de socios (mensajes internos de Koha; solo lectura) ─────────────────
@router.get("/notas", tags=["notas"])
async def notas_listar(
    dias: int = Query(30, ge=0, le=36500, description="Últimos N días. 0 = todo el historial"),
    q: str | None = Query(None, max_length=100, description="Texto a buscar en las notas"),
    tipo: str | None = Query(None, pattern="^(cuotas|reclamo|novedad)$"),
    pendientes: bool = Query(False, description="Solo novedades sin marcar como resueltas"),
    fresh: bool = Query(False),
    repo: KohaRepository = Depends(get_repository),
):
    """Notas de todos los socios, de la más nueva a la más vieja.

    El conteo por tipo es sobre el período y la búsqueda, antes de filtrar por tipo:
    así los botones de filtro muestran cuántas hay de cada una.
    """
    desde = date.today() - timedelta(days=dias) if dias else None
    texto = (q or "").strip() or None
    if texto:
        # Las búsquedas no se cachean: cada texto distinto sería una entrada nueva.
        filas = await repo.recent_notes(desde, texto)
    else:
        key = f"notas:{desde.isoformat() if desde else 'todo'}"
        if fresh:
            cache.invalidate(key)
        filas = await cache.cached(key, TTL_NOTAS, lambda: repo.recent_notes(desde))

    items = notas.armar(filas)
    conteo = Counter(n["tipo"] for n in items)
    pendientes_total = sum(1 for n in items if n["tipo"] == notas.NOVEDAD and not n["resuelta"])
    if tipo:
        items = [n for n in items if n["tipo"] == tipo]
    if pendientes:              # "pendiente" solo tiene sentido para una novedad
        items = [n for n in items if n["tipo"] == notas.NOVEDAD and not n["resuelta"]]
    return {
        "items": items,
        "conteo": {t: conteo.get(t, 0) for t in notas.ETIQUETAS},
        "novedades_pendientes": pendientes_total,
        "desde": desde.isoformat() if desde else None,
        "limite_alcanzado": len(filas) >= notas.LIMITE,
    }


@router.get("/notas/avisos", tags=["notas"])
async def notas_avisos(fresh: bool = Query(False), repo: KohaRepository = Depends(get_repository)):
    """Carnet → novedades sin resolver de los últimos días.

    Lo usan Préstamos, Mails y Automáticos para mostrar un 📝 al lado del socio: si
    avisó "lo devuelvo en noviembre", que se vea antes de mandarle un reclamo.
    """
    desde = date.today() - timedelta(days=AVISOS_DIAS)
    if fresh:
        cache.invalidate("notas_avisos")
    filas = await cache.cached("notas_avisos", TTL_AVISOS,
                               lambda: repo.recent_notes(desde), swr=not fresh)
    return {"dias": AVISOS_DIAS,
            "items": notas.avisos_por_socio(notas.armar(filas), AVISOS_DIAS)}


@router.post("/notas/{nota_id}/resuelta", tags=["notas"])
async def notas_marcar(nota_id: str, body: dict = Body(default={}),
                       s: Sesion = Depends(requiere(permisos.KOHA))):
    """Marca (o desmarca con {"resuelta": false}) una novedad como resuelta.

    La marca vive en la app: Koha no tiene dónde guardarla. La nota no se toca.
    """
    resuelta = bool((body or {}).get("resuelta", True))
    try:
        marca = notas.marcar(nota_id, resuelta, s.nombre or s.usuario)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": nota_id, "resuelta": resuelta,
            "resuelta_por": marca.get("por", ""), "resuelta_cuando": marca.get("cuando", "")}


# ── Mails ─────────────────────────────────────────────────────────────────────
@router.get("/mail/config", tags=["mail"])
async def mail_config(_: Sesion = Depends(requiere(permisos.MAILS))):
    """Estado de la configuración de mail (sin exponer credenciales)."""
    return {
        "configured": bool(settings.smtp_host),
        "from": settings.smtp_from or settings.smtp_user,
        "from_name": settings.smtp_from_name,
        "dry_run_default": settings.mail_dry_run,
    }


@router.post("/mail/send", tags=["mail"])
async def mail_send(body: MailSendRequest, ses: Sesion = Depends(requiere(permisos.MAILS))):
    """Envía (o simula) una campaña de mail a los destinatarios seleccionados.

    Variables de combinación en asunto/cuerpo: {{nombre}}, {{apellido}}, {{carnet}},
    {{email}} (y las que se pasen por destinatario). Cada socio puede personalizarse.

    Queda registrado en el historial (igual que los automáticos): quién lo mandó,
    a quiénes, con qué mensaje y con qué resultado.
    """
    dry_run = settings.mail_dry_run if body.dry_run is None else body.dry_run
    recipients = [r.model_dump() for r in body.recipients]
    if body.test_to:
        recipients = recipients[:1]   # prueba: una sola muestra (igual que send_campaign)

    # Tablas HTML unificadas de libros (misma presentación que los automáticos).
    # Los vencidos van SIN fecha: al socio se le nombra el libro, no cuándo se le pasó.
    for r in recipients:
        loans = r.get("loans")
        if loans:
            blocks = {k: mail.libros_table(v, con_fecha=(k != "vencidos"))
                      for k, v in loans.items() if isinstance(v, list) and v}
            if blocks:
                r["html"] = blocks

    # Enriquece con la deuda de cuota por carnet, así {{meses_debe}}/{{meses_impagos}}
    # funcionan aunque el socio se haya agregado por búsqueda (no solo desde el cruce).
    usa_cuota = "{{meses_debe}}" in (body.body or "") or "{{meses_impagos}}" in (body.body or "")
    if cuotas.configured() and usa_cuota:
        try:
            import asyncio
            data = await asyncio.to_thread(cuotas.estado_cuotas, max(cuotas.anios_disponibles()))
            cmap = {_norm_id(s["matricula"]): s for s in data["socios"] if s.get("matricula")}
            for r in recipients:
                v = r.get("vars") or {}
                s = cmap.get(_norm_id(v.get("carnet")))
                if s:
                    v["meses_debe"] = str(s.get("debe", 0))
                    v["meses_impagos"] = ", ".join(s.get("impagos", [])) or "—"
                    r["vars"] = v
        except Exception as exc:  # noqa: BLE001
            logger.warning("No se pudo enriquecer cuotas en mail: %s", exc)

    run_id = historial.new_run_id()
    seguimiento = historial.aplicar_seguimiento(recipients, run_id)
    comun = {"run_id": run_id, "origen": "manual", "titulo": body.subject or "(sin asunto)",
             "tipo": "socios", "trigger": "prueba" if body.test_to else "manual",
             "usuario": ses.usuario, "test_to": body.test_to, "dry_run": dry_run,
             "subject_tpl": body.subject, "body_tpl": body.body}
    try:
        res = await mail.send_campaign(
            subject_tpl=body.subject,
            body_tpl=body.body,
            recipients=recipients,
            dry_run=dry_run,
            test_to=body.test_to,
        )
    except RuntimeError as exc:
        historial.record(**comun, dests=[], ok=False, error=str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # cualquier otro error: 502 con mensaje, nunca 500 crudo
        logger.exception("mail/send falló")
        historial.record(**comun, dests=[], ok=False, error=str(exc))
        raise HTTPException(status_code=502, detail=f"Error al enviar: {exc}") from exc
    historial.record(**comun, seguimiento=seguimiento,
                     dests=historial.destinatarios(recipients, res.get("resultados", [])))
    return {**res, "run_id": run_id}


# ── Agenda de actividades (Google Calendar, solo lectura) ──────────────────────
@router.get("/agenda", tags=["agenda"])
async def agenda_events(
    desde: str | None = Query(None, description="YYYY-MM-DD (por defecto hoy)"),
    dias: int = Query(90, ge=1, le=400),
    fresh: bool = Query(False),
    _: Sesion = Depends(requiere(permisos.CALENDARIO_VER)),
):
    """Calendario unificado: los Google Calendar de la biblioteca + las reservas de
    espacio ya aprobadas en la app, mezcladas y ordenadas por fecha.

    Las reservas salen de la base propia, así que se ven al instante y no dependen
    de que Google conteste; si Google falla, el calendario igual muestra las reservas.
    """
    import datetime as _dt
    try:
        d1 = _dt.date.fromisoformat(desde) if desde else _dt.date.today()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Fecha inválida (YYYY-MM-DD).") from exc
    d2 = d1 + _dt.timedelta(days=dias)

    reservas = solicitudes.eventos(d1, d2)

    evs, aviso = [], None
    if agenda.configured():
        key = f"agenda:{d1.isoformat()}:{d2.isoformat()}"
        if fresh:
            cache.invalidate(key)
        try:
            evs = await cache.cached(key, TTL_AGENDA, lambda: agenda.events(d1, d2), swr=not fresh)
        except Exception as exc:  # noqa: BLE001
            # Que Google falle no puede dejar sin calendario a la biblioteca.
            logger.warning("agenda: no se pudo leer Google Calendar: %s", exc)
            aviso = f"No se pudieron leer los calendarios de Google ({exc}). "\
                    "Las reservas de la app sí se están mostrando."

    todos = sorted([*evs, *reservas], key=lambda e: e.get("inicio") or "")
    calendarios = agenda.calendars() if agenda.configured() else []
    if reservas or espacios.listar():
        calendarios = [*calendarios,
                       {"nombre": "Reservas de espacio", "color": solicitudes.COLOR_RESERVA}]
    return {"configured": bool(agenda.configured() or reservas),
            "desde": d1.isoformat(), "hasta": d2.isoformat(),
            "calendarios": calendarios, "events": todos, "aviso": aviso}


# ── Espacios que se pueden reservar ───────────────────────────────────────────
@router.get("/espacios", tags=["espacios"])
async def espacios_listar(todos: bool = Query(False),
                          s: Sesion = Depends(requiere(permisos.CALENDARIO_VER))):
    """Lista de espacios. `todos=1` incluye los dados de baja (solo para editar)."""
    incluir = todos and s.puede(permisos.CALENDARIO_EDITAR)
    return {"items": espacios.listar(incluir_inactivos=incluir),
            "puede_editar": s.puede(permisos.CALENDARIO_EDITAR)}


@router.post("/espacios", tags=["espacios"])
async def espacios_crear(body: dict = Body(...),
                         _: Sesion = Depends(requiere(permisos.CALENDARIO_EDITAR))):
    b = body or {}
    try:
        return espacios.crear(b.get("nombre", ""), b.get("capacidad", 0), b.get("notas", ""))
    except espacios.ErrorEspacio as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/espacios/{eid}", tags=["espacios"])
async def espacios_actualizar(eid: str, body: dict = Body(...),
                              _: Sesion = Depends(requiere(permisos.CALENDARIO_EDITAR))):
    try:
        return espacios.actualizar(eid, body or {})
    except espacios.ErrorEspacio as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/espacios/{eid}", tags=["espacios"])
async def espacios_borrar(eid: str, _: Sesion = Depends(requiere(permisos.CALENDARIO_EDITAR))):
    """Baja definitiva. Si tiene reservas hechas, conviene desactivarlo en vez de borrar."""
    try:
        espacios.borrar(eid)
    except espacios.ErrorEspacio as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


# ── Solicitudes de espacio ────────────────────────────────────────────────────
def _mia(s: Sesion, sol: dict) -> bool:
    """¿Esta solicitud es de quien la está mirando (o de su subcomisión)?"""
    quien = sol.get("solicitante") or {}
    if s.uid and quien.get("uid") == s.uid:
        return True
    return bool(s.subcomision) and quien.get("subcomision") == s.subcomision


def _ver_o_404(s: Sesion, sid: str) -> dict:
    """Trae la solicitud si esta persona tiene por qué verla."""
    sol = solicitudes.obtener(sid)
    if sol is None:
        raise HTTPException(status_code=404, detail="Esa solicitud no existe.")
    if not (s.puede(permisos.SOLICITUDES_RESOLVER) or _mia(s, sol)):
        raise HTTPException(status_code=404, detail="Esa solicitud no existe.")
    return sol


@router.get("/solicitudes", tags=["solicitudes"])
async def solicitudes_listar(estado: str | None = Query(None),
                             s: Sesion = Depends(requiere(permisos.SOLICITUDES_CREAR))):
    """Bandeja de solicitudes.

    Quien resuelve ve todas; una subcomisión ve solo las suyas.
    """
    if s.puede(permisos.SOLICITUDES_RESOLVER):
        items = solicitudes.listar(estado=estado)
    else:
        items = [x for x in solicitudes.listar(estado=estado) if _mia(s, x)]
    return {
        "items": [solicitudes.con_espacio(x) for x in items],
        "puede_resolver": s.puede(permisos.SOLICITUDES_RESOLVER),
        "pendientes": solicitudes.pendientes() if s.puede(permisos.SOLICITUDES_RESOLVER) else 0,
        "estados": [{"id": e, "titulo": solicitudes.ETIQUETAS[e]} for e in solicitudes.ESTADOS],
        "repeticiones": [{"id": k, "titulo": v} for k, v in solicitudes.REPETICIONES.items()],
    }


@router.get("/solicitudes/{sid}", tags=["solicitudes"])
async def solicitudes_detalle(sid: str, s: Sesion = Depends(requiere(permisos.SOLICITUDES_CREAR))):
    return solicitudes.con_espacio(_ver_o_404(s, sid))


@router.post("/solicitudes/conflictos", tags=["solicitudes"])
async def solicitudes_conflictos(body: dict = Body(...),
                                 _: Sesion = Depends(requiere(permisos.CALENDARIO_VER))):
    """Avisa si lo que se está por pedir se pisa con algo ya aprobado. No bloquea."""
    b = body or {}
    try:
        fechas = solicitudes.ocurrencias(b.get("inicio"), b.get("fin"),
                                         b.get("repeticion") or solicitudes.UNICA,
                                         b.get("hasta"))
    except solicitudes.ErrorSolicitud as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    choques = solicitudes.conflictos(b.get("espacio_id", ""), fechas, b.get("excluir_id"))
    return {"fechas": fechas, "conflictos": choques}


@router.post("/solicitudes", tags=["solicitudes"])
async def solicitudes_crear(body: dict = Body(...),
                            s: Sesion = Depends(requiere(permisos.SOLICITUDES_CREAR))):
    try:
        creada = solicitudes.crear(body or {}, {
            "uid": s.uid, "usuario": s.usuario, "nombre": s.nombre, "subcomision": s.subcomision})
    except (solicitudes.ErrorSolicitud, espacios.ErrorEspacio) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return solicitudes.con_espacio(creada)


@router.put("/solicitudes/{sid}", tags=["solicitudes"])
async def solicitudes_editar(sid: str, body: dict = Body(...),
                             s: Sesion = Depends(requiere(permisos.SOLICITUDES_CREAR))):
    """Corregir el pedido. Solo quien lo hizo (o quien resuelve), y solo sin resolver."""
    sol = _ver_o_404(s, sid)
    if not (s.puede(permisos.SOLICITUDES_RESOLVER) or _mia(s, sol)):
        raise HTTPException(status_code=403, detail="Solo podés editar tus propias solicitudes.")
    try:
        return solicitudes.con_espacio(solicitudes.editar(sid, body or {}, por=s.usuario))
    except (solicitudes.ErrorSolicitud, espacios.ErrorEspacio) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/solicitudes/{sid}/resolver", tags=["solicitudes"])
async def solicitudes_resolver(sid: str, body: dict = Body(...),
                               s: Sesion = Depends(requiere(permisos.SOLICITUDES_RESOLVER))):
    """Aprobar (pudiendo ajustar fecha/horario/espacio), rechazar o pedir cambios."""
    _ver_o_404(s, sid)
    b = body or {}
    try:
        r = solicitudes.resolver(sid, b.get("decision", ""), por=s.usuario,
                                 motivo=b.get("motivo", ""), cambios=b.get("cambios") or None)
    except (solicitudes.ErrorSolicitud, espacios.ErrorEspacio) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return solicitudes.con_espacio(r)


@router.post("/solicitudes/{sid}/cancelar", tags=["solicitudes"])
async def solicitudes_cancelar(sid: str, body: dict = Body(default={}),
                               s: Sesion = Depends(requiere(permisos.SOLICITUDES_CREAR))):
    """Dar de baja. Quien la pidió puede cancelar la suya; quien resuelve, cualquiera."""
    sol = _ver_o_404(s, sid)
    if not (s.puede(permisos.SOLICITUDES_RESOLVER) or _mia(s, sol)):
        raise HTTPException(status_code=403, detail="Solo podés cancelar tus propias solicitudes.")
    try:
        return solicitudes.con_espacio(
            solicitudes.cancelar(sid, por=s.usuario, motivo=(body or {}).get("motivo", "")))
    except solicitudes.ErrorSolicitud as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ── Cuotas societarias (planilla de Google, solo lectura) ──────────────────────
@router.get("/cuotas", tags=["cuotas"])
async def cuotas_estado(
    anio: int = Query(None, description="Año (por defecto el más reciente)"),
    fresh: bool = Query(False),
    _: Sesion = Depends(requiere(permisos.KOHA)),
):
    """Estado de cuotas de todos los socios para un año."""
    if not cuotas.configured():
        return {"configured": False}
    import asyncio
    if fresh:
        cuotas.clear_cache()
        cache.invalidate("cruce_members")  # el cruce depende de la planilla
    try:
        data = await asyncio.to_thread(cuotas.estado_cuotas, anio or max(cuotas.anios_disponibles()))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"No se pudo leer la planilla: {exc}") from exc
    data["configured"] = True
    return data


@router.post("/cuotas/pago", tags=["cuotas"])
async def cuotas_pago(body: dict = Body(...), ses: Sesion = Depends(requiere(permisos.KOHA))):
    """Carga (o quita) un pago de cuota desde la app: {matricula, anio, mes, pagado}.

    Se guarda en la base de la app y se superpone a la planilla (que no se toca).
    """
    mat = str((body or {}).get("matricula") or "").strip()
    try:
        anio = int((body or {}).get("anio"))
        mes = int((body or {}).get("mes"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="anio y mes deben ser números.")
    pagado = bool((body or {}).get("pagado"))
    try:
        res = await asyncio.to_thread(pagos.set_pago, mat, anio, mes, pagado, ses.usuario)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, **res}


@router.get("/cuotas/pagos", tags=["cuotas"])
async def cuotas_pagos_all(_: Sesion = Depends(requiere(permisos.KOHA))):
    """Todos los pagos cargados desde la app (registro/auditoría)."""
    return {"pagos": await asyncio.to_thread(pagos.all_pagos)}


# ── Cruce de datos: Koha (préstamos) vs planilla (cuotas) ──────────────────────
def _norm_id(x) -> str:
    x = str(x or "").strip()
    return x.lstrip("0") or x


@router.get("/cruce", tags=["cuotas"])
async def cruce(fresh: bool = Query(False), repo: KohaRepository = Depends(get_repository),
                _: Sesion = Depends(requiere(permisos.KOHA))):
    """Cruza socios de Koha (actividad de préstamo) con la planilla de cuotas (matrícula=carnet)."""
    if not cuotas.configured():
        return {"configured": False}
    import asyncio
    sql = """SELECT br.cardnumber, br.surname, br.firstname,
      COALESCE(NULLIF(TRIM(br.email),''), NULLIF(TRIM(br.emailpro),''), NULLIF(TRIM(br.B_email),'')) AS email,
      br.categorycode, c.description AS categoria,
      (SELECT COUNT(*) FROM statistics s WHERE s.borrowernumber=br.borrowernumber
        AND s.type='issue' AND s.datetime >= NOW() - INTERVAL 1 YEAR) AS l12
      FROM borrowers br LEFT JOIN categories c ON c.categorycode = br.categorycode"""
    if fresh:
        cache.invalidate("cruce_members")
    # Koha (SQL) y la planilla de cuotas (Google Sheets) son independientes → en paralelo.
    koha, data = await asyncio.gather(
        cache.cached("cruce_members", TTL_CRUCE, lambda: repo.run_sql(sql), swr=not fresh),
        asyncio.to_thread(cuotas.estado_cuotas, max(cuotas.anios_disponibles())),
    )

    # Socios "de baja" en Koha (categoría B). No cuentan ni reciben recordatorios.
    BAJA = {"B"}
    koha_by = {_norm_id(r["cardnumber"]): r for r in koha
               if r.get("cardnumber") and (r.get("categorycode") or "").strip() not in BAJA}
    pl_by = {_norm_id(s["matricula"]): s for s in data["socios"] if s.get("matricula")}
    ks, ps = set(koha_by), set(pl_by)
    inter = ks & ps

    def retira(card):
        r = koha_by.get(card)
        try:
            return r is not None and int(r["l12"]) > 0
        except (TypeError, ValueError):
            return False

    NO_CUOTA = {"BEC."}  # becados no pagan cuota → nunca figuran como deudores

    def info(card, s=None):
        k = koha_by.get(card, {})
        return {"carnet": k.get("cardnumber") or (s["matricula"] if s else ""),
                "apellido": k.get("surname") or (s["apellido"] if s else ""),
                "nombre": k.get("firstname") or (s["nombre"] if s else ""),
                "email": k.get("email") or "",
                "categoria": k.get("categoria") or (k.get("categorycode") or "")}

    # Lista completa de socios que coinciden, con meses adeudados y si retira.
    # El frontend arma la matriz/listas aplicando un umbral configurable de meses.
    matched = []
    for m in inter:
        k = koha_by[m]
        becado = (k.get("categorycode") or "").strip() in NO_CUOTA
        d = info(m, pl_by[m])
        d["debe"] = 0 if becado else pl_by[m].get("debe", 0)
        d["impagos"] = [] if becado else pl_by[m].get("impagos", [])
        d["retira"] = retira(m)
        matched.append(d)

    return {
        "configured": True,
        "anio": data["anio"],
        "koha_total": len(koha_by),
        "planilla_total": len(pl_by),
        "coinciden": len(inter),
        "solo_koha": len(ks - ps),
        "solo_planilla": len(ps - ks),
        "matched": matched,
        "retiran_sin_planilla": [info(k) for k in (ks - ps) if retira(k)],
    }


# ── Envíos automáticos (lista de reportes configurables) ───────────────────────
@router.get("/auto/config", tags=["auto"])
async def auto_config_get(_: Sesion = Depends(requiere(permisos.MAILS))):
    """Lista de reportes automáticos + última ejecución de cada uno."""
    return auto_mail.load_config()


@router.post("/auto/report", tags=["auto"])
async def auto_report_add(body: dict = Body(...), _: Sesion = Depends(requiere(permisos.MAILS))):
    """Crea un reporte nuevo. body: {tipo: 'interno'|'socios', nombre}."""
    return auto_mail.add_report((body or {}).get("tipo", "interno"), (body or {}).get("nombre", ""))


@router.put("/auto/report/{rid}", tags=["auto"])
async def auto_report_update(rid: str, partial: dict = Body(...),
                             _: Sesion = Depends(requiere(permisos.MAILS))):
    """Actualiza (merge) la configuración de un reporte."""
    return auto_mail.update_report(rid, partial)


@router.delete("/auto/report/{rid}", tags=["auto"])
async def auto_report_delete(rid: str, _: Sesion = Depends(requiere(permisos.MAILS))):
    return auto_mail.delete_report(rid)


@router.get("/auto/preview/{rid}", tags=["auto"])
async def auto_preview(rid: str, _: Sesion = Depends(requiere(permisos.MAILS))):
    """Vista previa de lo que se enviaría (sin enviar nada)."""
    rep = auto_mail.get_report(rid)
    if not rep:
        raise HTTPException(status_code=404, detail="Reporte desconocido.")
    try:
        return await auto_mail.preview_report(rep)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/auto/run/{rid}", tags=["auto"])
async def auto_run(rid: str, body: dict = Body(default={}),
                   ses: Sesion = Depends(requiere(permisos.MAILS))):
    """Ejecuta un reporte ahora. Si se pasa test_to, manda una prueba a esa dirección."""
    rep = auto_mail.get_report(rid)
    if not rep:
        raise HTTPException(status_code=404, detail="Reporte desconocido.")
    try:
        return await auto_mail.run_report(rep, test_to=(body or {}).get("test_to") or None,
                                          usuario=ses.usuario)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/auto/history/{rid}", tags=["auto"])
async def auto_history(rid: str, limit: int = Query(20, ge=1, le=100),
                       _: Sesion = Depends(requiere(permisos.MAILS))):
    """Historial de ejecuciones de un reporte: cuándo, cómo se disparó y a quién se envió."""
    return {"items": auto_mail.get_history(rid, limit)}


# ── Historial de envíos (automáticos + manuales) ──────────────────────────────
@router.get("/envios", tags=["envios"])
async def envios_listar(origen: str | None = Query(None, pattern="^(auto|manual)$"),
                        report_id: str | None = Query(None),
                        limit: int = Query(50, ge=1, le=200),
                        _: Sesion = Depends(requiere(permisos.MAILS))):
    """Lista de envíos hechos: los automáticos y los de la pestaña Mails."""
    return {"items": historial.listar(origen=origen, report_id=report_id, limit=limit),
            "seguimiento_activo": tracking.enabled()}


@router.get("/envios/{run_id}", tags=["envios"])
async def envios_detalle(run_id: str, _: Sesion = Depends(requiere(permisos.MAILS))):
    """Detalle de un envío: cada destinatario, el mensaje que recibió y si lo abrió."""
    run = historial.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Envío no encontrado (puede haber sido podado).")
    return run


# Píxel de apertura. PÚBLICO a propósito: lo pide el cliente de correo del socio,
# que no tiene sesión. El token va firmado, así que no se pueden falsear aperturas.
_PIXEL_GIF = bytes.fromhex(
    "47494638396101000100800000000000ffffff21f90401000000002c00000000010001000002024401003b")


@router.get("/t/{token}", include_in_schema=False)
async def tracking_pixel(token: str):
    """Devuelve un GIF de 1×1 y registra la apertura. Nunca falla: si el token es
    inválido o el envío ya se podó, igual devuelve la imagen (no filtra nada)."""
    try:
        parsed = tracking.parse_token(token)
        if parsed:
            # En un hilo: escribe en la base y no queremos frenar el event loop.
            await asyncio.to_thread(historial.record_open, *parsed)
    except Exception as exc:  # noqa: BLE001
        logger.warning("No se pudo registrar la apertura: %s", exc)
    return Response(content=_PIXEL_GIF, media_type="image/gif", headers={
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache", "Expires": "0",
    })


# ── Warmup: precalienta los cachés más consultados al arrancar ────────────────
async def warmup() -> None:
    """Precarga en segundo plano los datos que gatean las primeras pantallas
    (préstamos y cuotas), para que la primera visita tras un redeploy no espere.

    Tolerante: cualquier fallo se registra y no afecta el arranque de la app.
    """
    if not (settings.koha_user and settings.koha_password):
        logger.info("warmup: sin credenciales de servicio de Koha, se omite.")
        return
    client = KohaClient(settings.koha_base_url, settings.koha_user, settings.koha_password)
    try:
        await client.login()
        repo = KohaRepository(client)
        try:
            await cache.cached("loans_contact", TTL_LOANS, repo.loans_contact)
            logger.info("warmup: préstamos precargados.")
        except Exception as exc:  # noqa: BLE001
            logger.warning("warmup préstamos: %s", exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("warmup: no se pudo iniciar sesión en Koha: %s", exc)
        return
    finally:
        await client.aclose()

    if cuotas.configured():
        try:
            await asyncio.to_thread(cuotas.estado_cuotas, max(cuotas.anios_disponibles()))
            logger.info("warmup: cuotas precargadas.")
        except Exception as exc:  # noqa: BLE001
            logger.warning("warmup cuotas: %s", exc)
