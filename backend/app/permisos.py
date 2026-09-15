"""Roles y permisos de la app.

Un **rol** es lo que la persona es en la biblioteca; un **permiso** es lo que puede
hacer. Los endpoints piden permisos, no roles: así, cuando cambia quién puede hacer
qué, se toca solo la tabla `ROLES` de este archivo y nada más.

Roles:
  · `bibliotecaria` — personal de la biblioteca. Entra con su usuario de Koha.
  · `comision`      — comisión directiva. Usuario propio de la app.
                      Decisión tomada: ve lo mismo que las bibliotecarias, menos
                      el pizarrón semanal, que es solo del equipo de la biblioteca.
  · `subcomision`   — cultura, prensa, infantil, huerta… Usuario propio de la app.
                      Solo lo institucional: calendario, inventario y talleres para
                      mirar, y sus propias solicitudes de espacio.
"""
from __future__ import annotations

from fastapi import HTTPException, status

# ── Permisos ────────────────────────────────────────────────────────────────
KOHA = "koha"                              # préstamos, socios, cuotas, estadísticas
MAILS = "mails"                            # mails, automáticos, historial de envíos
CALENDARIO_VER = "calendario.ver"
CALENDARIO_EDITAR = "calendario.editar"
SOLICITUDES_CREAR = "solicitudes.crear"
SOLICITUDES_RESOLVER = "solicitudes.resolver"   # aprobar / rechazar / reprogramar
INVENTARIO_VER = "inventario.ver"
INVENTARIO_EDITAR = "inventario.editar"
TALLERES_VER = "talleres.ver"
TALLERES_EDITAR = "talleres.editar"
USUARIOS_ADMIN = "usuarios.admin"
PIZARRON = "pizarron"                      # el pizarrón semanal del equipo de la biblioteca

_TODO = {
    KOHA, MAILS,
    CALENDARIO_VER, CALENDARIO_EDITAR,
    SOLICITUDES_CREAR, SOLICITUDES_RESOLVER,
    INVENTARIO_VER, INVENTARIO_EDITAR,
    TALLERES_VER, TALLERES_EDITAR,
    USUARIOS_ADMIN,
}

ROLES: dict[str, set[str]] = {
    # El pizarrón es la única excepción a "la comisión ve lo mismo que las
    # bibliotecarias" (decisión tomada): es el espacio de trabajo del equipo.
    "bibliotecaria": _TODO | {PIZARRON},
    "comision": set(_TODO),
    "subcomision": {
        CALENDARIO_VER,
        SOLICITUDES_CREAR,
        INVENTARIO_VER,
        TALLERES_VER,
    },
}

ETIQUETAS = {
    "bibliotecaria": "Bibliotecaria",
    "comision": "Comisión Directiva",
    "subcomision": "Subcomisión",
}


def permisos_de(rol: str) -> set[str]:
    """Permisos de un rol. Un rol desconocido no puede nada (falla cerrado)."""
    return set(ROLES.get(rol, ()))


def puede(rol: str, permiso: str) -> bool:
    return permiso in permisos_de(rol)


# ── Secciones del menú ──────────────────────────────────────────────────────
# El frontend arma el menú con lo que devuelve /api/me, así nadie ve una pestaña
# que después le va a dar 403. La lista es también el orden en que se muestran.
#
# `grupo`: las secciones que comparten grupo se muestran juntas, plegadas bajo un
# solo botón. Así el menú no crece a lo largo a medida que sumamos módulos.
# `movil`: si va en la barra de abajo del celular (el resto queda en "Más").
SECCIONES: list[dict] = [
    {"id": "stats",      "titulo": "Inicio",      "permiso": KOHA,           "movil": True},
    {"id": "pizarron",   "titulo": "Pizarrón",    "permiso": PIZARRON,       "movil": True},
    {"id": "loans",      "titulo": "Préstamos",   "permiso": KOHA,           "movil": True},
    # La ficha y las notas de los socios, juntas: una nota lleva a la ficha y viceversa.
    {"id": "members",    "titulo": "Buscar",      "permiso": KOHA, "grupo": "Socios", "movil": True},
    {"id": "notas",      "titulo": "Notas",       "permiso": KOHA, "grupo": "Socios", "movil": True},
    {"id": "cuotas",     "titulo": "Cuotas",      "permiso": KOHA,           "movil": False},
    # El calendario y las solicitudes van juntos: se usan de a pares.
    {"id": "agenda",      "titulo": "Calendario",  "permiso": CALENDARIO_VER,   "grupo": "Agenda", "movil": True},
    {"id": "solicitudes", "titulo": "Solicitudes", "permiso": SOLICITUDES_CREAR, "grupo": "Agenda", "movil": True},

    # Todo lo que sale de la biblioteca hacia afuera, junto.
    {"id": "mails",      "titulo": "Escribir",    "permiso": MAILS, "grupo": "Envíos", "movil": False},
    {"id": "auto",       "titulo": "Automáticos", "permiso": MAILS, "grupo": "Envíos", "movil": False},
    {"id": "envios",     "titulo": "Historial",   "permiso": MAILS, "grupo": "Envíos", "movil": False},

    # Los tableros de análisis, juntos.
    {"id": "estrategia", "titulo": "Estrategia",  "permiso": KOHA,  "grupo": "Reportes", "movil": False},

    {"id": "usuarios",   "titulo": "Usuarios",    "permiso": USUARIOS_ADMIN, "movil": False},
]


def secciones_de(rol: str) -> list[dict]:
    """Secciones que le corresponden a un rol, en orden."""
    tiene = permisos_de(rol)
    return [s for s in SECCIONES if s["permiso"] in tiene]


def menu_de(rol: str) -> list[dict]:
    """El menú ya armado: entradas sueltas y grupos con sus hijas.

    Devuelve una lista de {tipo: "seccion"|"grupo", …} en orden. Un grupo que se
    queda con una sola hija se muestra igual como grupo: da lugar a las que vengan
    (reportes de catálogo, de socios…) sin volver a mover el menú de lugar.
    """
    menu: list[dict] = []
    por_grupo: dict[str, dict] = {}
    for s in secciones_de(rol):
        grupo = s.get("grupo")
        if not grupo:
            menu.append({"tipo": "seccion", **s})
            continue
        if grupo not in por_grupo:
            por_grupo[grupo] = {"tipo": "grupo", "titulo": grupo, "items": []}
            menu.append(por_grupo[grupo])
        por_grupo[grupo]["items"].append(s)
    return menu


# ── Guardas para los endpoints ──────────────────────────────────────────────
def exigir(rol: str, permiso: str) -> None:
    """Corta con 403 si el rol no tiene el permiso."""
    if not puede(rol, permiso):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tenés permiso para esta sección.",
        )
