"""Lo que anda pasando en la Bayer: el registro de actividades convertido en una mirada.

Toma **solo registros validados** (ver `registro.py`) de un período y arma lo que se
muestra en la sección: números con comparación contra el período anterior, evolución
mes a mes, qué tipos y temáticas convocan, a qué edades se llega, qué días y horarios
funcionan, cómo se entera la gente, cómo vienen los talleres y lo último que pasó.

Esta sección la ven **todos los roles**, subcomisiones incluidas, así que acá no entra
nada sensible: ni incidentes, ni contactos, ni valoraciones, ni dinero.

Los promedios se calculan por actividad (no totales): una charla con 40 personas no se
compara con un taller de 12 encuentros de 10.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta

from . import espacios, registro

PERIODOS = {
    "mes": "Este mes",
    "3m": "Últimos 3 meses",
    "12m": "Últimos 12 meses",
    "anio": "Este año",
    "anio_anterior": "El año pasado",
}
MESES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")
MESES_LARGO = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
               "septiembre", "octubre", "noviembre", "diciembre")


# ── Períodos ────────────────────────────────────────────────────────────────
def _inicio_mes(d: date) -> date:
    return d.replace(day=1)


def _sumar_meses(d: date, n: int) -> date:
    total = d.year * 12 + d.month - 1 + n
    return date(total // 12, total % 12 + 1, 1)


def periodo(clave: str, hoy: date | None = None) -> tuple[date, date]:
    """(desde, hasta) del período, con `hasta` como mucho hoy."""
    hoy = hoy or date.today()
    if clave == "mes":
        return _inicio_mes(hoy), hoy
    if clave == "3m":
        return _sumar_meses(_inicio_mes(hoy), -2), hoy
    if clave == "12m":
        return _sumar_meses(_inicio_mes(hoy), -11), hoy
    if clave == "anio":
        return date(hoy.year, 1, 1), hoy
    if clave == "anio_anterior":
        return date(hoy.year - 1, 1, 1), date(hoy.year - 1, 12, 31)
    raise ValueError(f"Período desconocido: '{clave}'.")


def anterior(desde: date, hasta: date) -> tuple[date, date]:
    """El período inmediatamente anterior, del mismo largo."""
    dias = (hasta - desde).days + 1
    return desde - timedelta(days=dias), desde - timedelta(days=1)


def _meses_entre(desde: date, hasta: date) -> list[str]:
    out, m = [], _inicio_mes(desde)
    while m <= hasta:
        out.append(f"{m.year}-{m.month:02d}")
        m = _sumar_meses(m, 1)
    return out


def etiqueta_mes(mes: str, largo: bool = False) -> str:
    anio, m = (int(x) for x in mes.split("-"))
    return f"{MESES_LARGO[m - 1]} {anio}" if largo else f"{MESES[m - 1]} {str(anio)[2:]}"


# ── Cuentas ─────────────────────────────────────────────────────────────────
def _talleres(regs: list[dict]) -> dict[str, list[dict]]:
    """Resúmenes mensuales agrupados por taller (por nombre), ordenados por mes."""
    por_taller: dict[str, list[dict]] = defaultdict(list)
    for r in regs:
        if r["clase"] == "taller":
            por_taller[registro.normalizar(r["datos"]["titulo"])].append(r)
    return {k: sorted(v, key=lambda r: r["datos"]["mes"]) for k, v in por_taller.items()}


def _numeros(regs: list[dict]) -> dict:
    actividades = [r for r in regs if r["clase"] == "actividad"]
    talleres = _talleres(regs)
    minutos = sum(registro.derivados(r).get("duracion_min") or 0 for r in actividades)
    return {
        "actividades": len(actividades),
        "personas": sum(r["datos"]["personas_total"] for r in actividades),
        "primera_vez": sum(r["datos"].get("primera_vez") or 0 for r in actividades),
        "horas": round(minutos / 60),
        "talleres": len(talleres),
        # Personas distintas no se pueden sumar entre meses: se toma el mejor mes de cada taller.
        "participantes_talleres": sum(max(r["datos"]["participantes"] for r in rs) for rs in talleres.values()),
        "encuentros": sum(r["datos"]["encuentros"] for rs in talleres.values() for r in rs),
        "instituciones": len({registro.normalizar(r["datos"].get("organiza_detalle", ""))
                              for r in actividades
                              if r["datos"].get("organiza") == "institucion" and r["datos"].get("organiza_detalle")}),
    }


def _ranking(contador: dict[str, dict], catalogo: dict) -> list[dict]:
    out = []
    for clave, v in contador.items():
        n = v["actividades"]
        out.append({"clave": clave, "etiqueta": catalogo.get(clave, clave), "actividades": n,
                    "personas": v["personas"], "promedio": round(v["personas"] / n) if n else 0})
    return sorted(out, key=lambda x: (-x["actividades"], -x["personas"], x["etiqueta"]))


def _tendencia(serie: list[int]) -> str | None:
    """Cómo viene un taller según sus participantes mes a mes (hace falta más de un mes)."""
    if len(serie) < 2 or not serie[0]:
        return None
    cambio = (serie[-1] - serie[0]) / serie[0]
    return "crece" if cambio >= 0.2 else "baja" if cambio <= -0.2 else "estable"


def armar(clave: str = "3m", hoy: date | None = None) -> dict:
    """Todo lo que muestra la sección para un período."""
    desde, hasta = periodo(clave, hoy)
    desde_ant, hasta_ant = anterior(desde, hasta)
    regs = registro.validados_entre(desde, hasta)
    previos = registro.validados_entre(desde_ant, hasta_ant)
    actividades = [r for r in regs if r["clase"] == "actividad"]
    talleres = _talleres(regs)

    ahora, antes = _numeros(regs), _numeros(previos)
    numeros = {k: {"valor": v, "anterior": antes[k]} for k, v in ahora.items()}

    # Mes a mes
    por_mes = {m: {"mes": m, "etiqueta": etiqueta_mes(m), "actividades": 0, "personas": 0,
                   "encuentros": 0, "participantes": 0} for m in _meses_entre(desde, hasta)}
    for r in actividades:
        m = por_mes.get(r["datos"]["fecha"][:7])
        if m:
            m["actividades"] += 1
            m["personas"] += r["datos"]["personas_total"]
    for rs in talleres.values():
        for r in rs:
            m = por_mes.get(r["datos"]["mes"])
            if m:
                m["encuentros"] += r["datos"]["encuentros"]
                m["participantes"] += r["datos"]["participantes"]

    # Tipos, temáticas, espacios, organización y difusión
    tipos: dict[str, dict] = defaultdict(lambda: {"actividades": 0, "personas": 0})
    tematicas: dict[str, dict] = defaultdict(lambda: {"actividades": 0, "personas": 0})
    lugares: dict[str, dict] = defaultdict(lambda: {"actividades": 0, "personas": 0, "minutos": 0})
    organiza, difusion = Counter(), Counter()
    con_difusion = 0
    for r in actividades:
        d, extra = r["datos"], registro.derivados(r)
        tipos[d["tipo"]]["actividades"] += 1
        tipos[d["tipo"]]["personas"] += d["personas_total"]
        for t in d.get("tematicas", []):
            tematicas[t]["actividades"] += 1
            tematicas[t]["personas"] += d["personas_total"]
        lugar = espacios.nombre_de(d["espacio_id"]) if d.get("espacio_id") else (d.get("espacio_otro") or "Otro lugar")
        lugares[lugar]["actividades"] += 1
        lugares[lugar]["personas"] += d["personas_total"]
        lugares[lugar]["minutos"] += extra.get("duracion_min") or 0
        organiza[d.get("organiza") or "biblioteca"] += 1
        if d.get("difusion"):
            con_difusion += 1
            difusion.update(d["difusion"])
    for rs in talleres.values():                       # cada taller cuenta una vez en su temática
        ultimo = rs[-1]["datos"]
        for t in ultimo.get("tematicas", []):
            tematicas[t]["actividades"] += 1
            tematicas[t]["personas"] += max(r["datos"]["participantes"] for r in rs)

    # Edades: suma de lo informado. Se aclara cuánto del público tiene la edad cargada.
    edades = Counter()
    for r in actividades:
        edades.update(r["datos"].get("franjas") or {})
    for rs in talleres.values():
        edades.update(rs[-1]["datos"].get("franjas") or {})
    con_edad = sum(1 for r in actividades if r["datos"].get("franjas"))

    # Días y momentos del día
    grilla = {(d, m): {"actividades": 0, "personas": 0}
              for d in range(7) for m in registro.MOMENTOS}
    for r in actividades:
        extra = registro.derivados(r)
        celda = grilla[(extra["dia_semana"], extra["momento"])]
        celda["actividades"] += 1
        celda["personas"] += r["datos"]["personas_total"]

    # Talleres mes a mes
    lista_talleres = []
    for rs in talleres.values():
        ultimo = rs[-1]["datos"]
        serie = [r["datos"]["participantes"] for r in rs]
        lista_talleres.append({
            "titulo": ultimo["titulo"],
            "tallerista": ultimo.get("tallerista", ""),
            "meses": [{"mes": r["datos"]["mes"], "etiqueta": etiqueta_mes(r["datos"]["mes"]),
                       "participantes": r["datos"]["participantes"], "encuentros": r["datos"]["encuentros"],
                       "suspendidos": r["datos"].get("suspendidos") or 0,
                       "altas": r["datos"].get("altas"), "bajas": r["datos"].get("bajas")} for r in rs],
            "participantes": serie[-1],
            "encuentros": sum(r["datos"]["encuentros"] for r in rs),
            "tendencia": _tendencia(serie),
            "trabajado": ultimo.get("trabajado", ""),
        })
    lista_talleres.sort(key=lambda t: (-t["participantes"], t["titulo"]))

    # Lo último que pasó (sin datos sensibles)
    recientes = []
    for r in sorted(actividades, key=lambda r: (r["datos"]["fecha"], r["datos"]["hora_inicio"]), reverse=True)[:8]:
        d = r["datos"]
        recientes.append({
            "titulo": d["titulo"], "fecha": d["fecha"], "hora_inicio": d["hora_inicio"],
            "tipo": registro.TIPOS.get(d["tipo"], ""),
            "tematicas": [registro.TEMATICAS[t] for t in d.get("tematicas", []) if t in registro.TEMATICAS],
            "personas": d["personas_total"],
            "lugar": espacios.nombre_de(d["espacio_id"]) if d.get("espacio_id") else d.get("espacio_otro", ""),
            "a_cargo": d.get("a_cargo", ""),
            "descripcion": d.get("descripcion", ""),
        })

    return {
        "periodo": {"clave": clave, "etiqueta": PERIODOS[clave],
                    "desde": desde.isoformat(), "hasta": hasta.isoformat()},
        "anterior": {"desde": desde_ant.isoformat(), "hasta": hasta_ant.isoformat()},
        "hay_datos": bool(regs),
        "numeros": numeros,
        "por_mes": list(por_mes.values()),
        "tipos": _ranking(tipos, registro.TIPOS),
        "tematicas": _ranking(tematicas, registro.TEMATICAS),
        "lugares": sorted(({"etiqueta": k, "actividades": v["actividades"], "personas": v["personas"],
                            "horas": round(v["minutos"] / 60, 1)} for k, v in lugares.items()),
                          key=lambda x: (-x["actividades"], x["etiqueta"])),
        "organiza": [{"clave": k, "etiqueta": registro.ORGANIZA.get(k, k), "actividades": n}
                     for k, n in organiza.most_common()],
        "difusion": [{"clave": k, "etiqueta": registro.DIFUSION.get(k, k), "actividades": n}
                     for k, n in difusion.most_common()],
        "difusion_con_dato": con_difusion,
        "edades": [{"clave": k, "etiqueta": v, "personas": edades.get(k, 0)} for k, v in registro.FRANJAS.items()],
        "edades_con_dato": con_edad,
        "dias": [{"dia": d, "dia_nombre": registro.DIAS[d], "momento": m,
                  "momento_nombre": registro.MOMENTOS[m], **v} for (d, m), v in grilla.items()],
        "talleres": lista_talleres,
        "recientes": recientes,
        "destacados": destacados(actividades, lista_talleres, grilla),
    }


def destacados(actividades: list[dict], talleres: list[dict], grilla: dict) -> list[dict]:
    """Frases cortas con lo más llamativo del período. Solo si hay con qué decirlas."""
    out = []
    if actividades:
        top = max(actividades, key=lambda r: r["datos"]["personas_total"])
        d = top["datos"]
        out.append({"clave": "mas_publico",
                    "texto": f"La actividad con más público fue «{d['titulo']}»: "
                             f"{d['personas_total']} personas el {_dia_mes(d['fecha'])}."})

    por_tema: dict[str, list[int]] = defaultdict(list)
    for r in actividades:
        for t in r["datos"].get("tematicas", []):
            por_tema[t].append(r["datos"]["personas_total"])
    candidatos = [(sum(v) / len(v), t) for t, v in por_tema.items() if len(v) >= 2]
    if candidatos:
        prom, tema = max(candidatos)
        out.append({"clave": "tematica",
                    "texto": f"Lo que más convoca: {registro.TEMATICAS.get(tema, tema)}, "
                             f"con {round(prom)} personas por actividad en promedio."})

    nuevos = sum(r["datos"].get("primera_vez") or 0 for r in actividades)
    if nuevos:
        out.append({"clave": "primera_vez",
                    "texto": f"{nuevos} {'persona vino' if nuevos == 1 else 'personas vinieron'} "
                             f"por primera vez a la biblioteca."})

    celdas = [(v["actividades"], v["personas"], d, m) for (d, m), v in grilla.items() if v["actividades"] >= 2]
    if celdas:
        _, _, dia, momento = max(celdas)
        out.append({"clave": "dia",
                    "texto": f"El momento con más movimiento: {registro.DIAS[dia].lower()} "
                             f"a la {registro.MOMENTOS[momento].lower()}."})

    for t in talleres:
        if t["tendencia"] == "baja":
            primero, ultimo = t["meses"][0], t["meses"][-1]
            out.append({"clave": "taller_baja", "alerta": True,
                        "texto": f"«{t['titulo']}» viene bajando: de {primero['participantes']} "
                                 f"participantes en {etiqueta_mes(primero['mes'], True)} a "
                                 f"{ultimo['participantes']} en {etiqueta_mes(ultimo['mes'], True)}."})
    return out


def _dia_mes(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day} de {MESES_LARGO[d.month - 1]}"
