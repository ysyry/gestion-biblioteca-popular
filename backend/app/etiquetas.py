"""Las etiquetas de los mails ({{nombre}}, {{meses_debe}}…), en un solo lugar.

De acá salen los chips para insertar, la ayuda que explica cada una y la lista que
controlan los tests: cada etiqueta que se ofrece tiene que salir con su dato en cada
envío donde se ofrece. Así la pantalla y los mails no se desincronizan.

Dos juegos:
  · `SOCIO`   — un mail por socio: la pestaña Mails y los Automáticos a socios.
  · `INTERNO` — el resumen que reciben las bibliotecarias (Automáticos internos).
"""
from __future__ import annotations

import re

SOCIO: list[dict] = [
    {"grupo": "Datos del socio", "items": [
        ("nombre", "El nombre del socio."),
        ("apellido", "El apellido del socio."),
        ("carnet", "El número de socio (el carnet de Koha)."),
        ("email", "El mail del socio, al que se le manda."),
    ]},
    {"grupo": "Libros del socio", "items": [
        ("vencidos", "Los libros que el socio <b>ya tendría que haber devuelto</b>. Va la lista de títulos, <b>sin la fecha</b>."),
        ("activos", "Los que <b>todavía tiene en préstamo</b> y aún no vencieron. Va el título y <b>cuándo vence</b> cada uno."),
        ("prestamos", "<b>Todos los préstamos</b> del socio: los vencidos y los activos juntos, en una sola lista."),
        ("cantidad_vencidos", "Cuántos vencidos tiene (un número)."),
        ("cantidad_activos", "Cuántos activos tiene (un número)."),
        ("cantidad_prestamos", "Cuántos libros tiene en total (un número)."),
    ]},
    {"grupo": "Cuota societaria", "items": [
        ("meses_debe", "Cuántos meses de cuota debe <b>este año</b>, contando hasta el mes actual (un número). Sale de la planilla de pagos."),
        ("meses_impagos", "Cuáles son esos meses (ej.: Jul, Ago, Sep). Si no debe ninguno: —."),
        ("ultimo_mes_pago", "El <b>último mes que pagó</b>, mirando todos los años (ej.: agosto 2026). Si nunca pagó: “sin pagos registrados”."),
    ]},
]

INTERNO: list[dict] = [
    {"grupo": "Préstamos", "items": [
        ("fecha", "La fecha del día en que se envía el reporte (DD/MM/AAAA)."),
        ("dias_antes", "Los días configurados para “activos” (los que vencen dentro de esos días)."),
        ("total_vencidos", "Cuántos préstamos vencidos hay en total."),
        ("lista_vencidos", "Todos los préstamos vencidos de la biblioteca, con socio, fecha y días de atraso."),
        ("total_activos", "Cuántos préstamos activos vencen dentro de los días configurados."),
        ("lista_activos", "Esos préstamos activos, con socio y fecha de vencimiento."),
        ("total_socios_deben", "Cuántos socios distintos tienen algún préstamo vencido."),
    ]},
    {"grupo": "Cuotas", "items": [
        ("total_deudores_cuota", "Cuántos socios deben cuota (desde los meses configurados; sin bajas ni becados)."),
        ("lista_cuotas", "Esos socios, con cuántos meses deben, cuáles y su último mes pago."),
    ]},
]

# Nombres viejos que siguen andando si alguien los dejó escritos (no se ofrecen).
ALIAS = {"por_vencer": "activos", "cantidad_por_vencer": "cantidad_activos",
         "total_por_vencer": "total_activos", "lista_por_vencer": "lista_activos"}

CUOTA_SOCIO = {"meses_debe", "meses_impagos", "ultimo_mes_pago"}
CUOTA_INTERNO = {"total_deudores_cuota", "lista_cuotas"}

_ETIQUETA = re.compile(r"{{\s*(\w+)\s*}}")


def nombres(juego: list[dict]) -> list[str]:
    return [k for g in juego for k, _ in g["items"]]


def usadas(*textos: str | None) -> set[str]:
    """Qué etiquetas aparecen en estos textos (tolera espacios: {{ nombre }})."""
    return {m for t in textos if t for m in _ETIQUETA.findall(t)}


def catalogo() -> dict:
    """Para la pantalla: los chips y la ayuda de cada juego."""
    def armar(juego):
        return [{"grupo": g["grupo"], "items": [{"etiqueta": k, "descripcion": d} for k, d in g["items"]]}
                for g in juego]
    return {"socio": armar(SOCIO), "interno": armar(INTERNO)}


def cuota_socio(s: dict | None) -> dict:
    """Las etiquetas de cuota de un socio, desde su fila de la planilla (`cuotas.py`).

    Sin fila (no figura en la planilla) no hay de dónde sacar el dato: 0 y —.
    """
    if not s:
        return {"meses_debe": "0", "meses_impagos": "—", "ultimo_mes_pago": "—"}
    ultimo = s.get("ultimo_pago") or {}
    return {
        "meses_debe": str(s.get("debe", 0) or 0),
        "meses_impagos": ", ".join(s.get("impagos") or []) or "—",
        "ultimo_mes_pago": ultimo.get("texto") or "sin pagos registrados",
    }
