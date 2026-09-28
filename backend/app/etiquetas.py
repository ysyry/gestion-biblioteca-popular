"""Las etiquetas de los mails ({{nombre}}, {{meses_debe}}…), en un solo lugar.

De acá salen los chips para insertar, la ayuda que explica cada una y la lista que
controlan los tests: cada etiqueta que se ofrece tiene que salir con su dato en cada
envío donde se ofrece. Así la pantalla y los mails no se desincronizan.

Cada etiqueta lleva (nombre, qué pone, un ejemplo de cómo sale en el mail).

Dos juegos:
  · `SOCIO`   — un mail por socio: la pestaña Mails y los Automáticos a socios.
  · `INTERNO` — el resumen que reciben las bibliotecarias (Automáticos internos).
"""
from __future__ import annotations

import re

SOCIO: list[dict] = [
    {"grupo": "Datos del socio", "items": [
        ("nombre", "El nombre del socio.", "Ana"),
        ("apellido", "El apellido del socio.", "Pérez"),
        ("carnet", "El número de socio (el carnet de Koha).", "1234"),
        ("email", "El mail del socio, al que se le manda.", "ana@gmail.com"),
    ]},
    {"grupo": "Libros del socio", "items": [
        ("vencidos", "Los libros que el socio <b>ya tendría que haber devuelto</b>. Va la lista de títulos, <b>sin la fecha</b>.", "• Rayuela\n• Ficciones"),
        ("activos", "Los que <b>todavía tiene en préstamo</b> y aún no vencieron. Va el título y <b>cuándo vence</b> cada uno.", "• El Aleph (vence 15/10/2026)"),
        ("prestamos", "<b>Todos los préstamos</b> del socio: los vencidos y los activos juntos, en una sola lista.", "• Rayuela (vence 18/09/2026)\n• Ficciones (vence 20/09/2026)\n• El Aleph (vence 15/10/2026)"),
        ("cantidad_vencidos", "Cuántos vencidos tiene (un número).", "2"),
        ("cantidad_activos", "Cuántos activos tiene (un número).", "1"),
        ("cantidad_prestamos", "Cuántos libros tiene en total (un número).", "3"),
    ]},
    {"grupo": "Cuota societaria", "items": [
        ("meses_debe", "Cuántos meses de cuota debe <b>este año</b>, contando hasta el mes actual (un número). Sale de la planilla de pagos.", "3"),
        ("meses_impagos", "Cuáles son esos meses (ej.: Jul, Ago, Sep). Si no debe ninguno: —.", "Jul, Ago, Sep"),
        ("ultimo_mes_pago", "El <b>último mes que pagó</b>, mirando todos los años (ej.: agosto 2026). Si nunca pagó: “sin pagos registrados”.", "junio 2026"),
    ]},
]

INTERNO: list[dict] = [
    {"grupo": "Préstamos", "items": [
        ("fecha", "La fecha del día en que se envía el reporte (DD/MM/AAAA).", "28/09/2026"),
        ("dias_antes", "Los días configurados para “activos” (los que vencen dentro de esos días).", "7"),
        ("total_vencidos", "Cuántos préstamos vencidos hay en total.", "12"),
        ("lista_vencidos", "Todos los préstamos vencidos de la biblioteca, con socio, fecha y días de atraso.", "• N° 1234 — Pérez, Ana — Rayuela (venció 18/09/2026, 10 días)"),
        ("total_activos", "Cuántos préstamos activos vencen dentro de los días configurados.", "5"),
        ("lista_activos", "Esos préstamos activos, con socio y fecha de vencimiento.", "• N° 5678 — Gómez, Bruno — Ficciones (vence 01/10/2026)"),
        ("total_socios_deben", "Cuántos socios distintos tienen algún préstamo vencido.", "8"),
    ]},
    {"grupo": "Cuotas", "items": [
        ("total_deudores_cuota", "Cuántos socios deben cuota (desde los meses configurados; sin bajas ni becados).", "15"),
        ("lista_cuotas", "Esos socios, con cuántos meses deben, cuáles y su último mes pago.", "• Pérez, Ana (mat. 1234) — debe 3: Jul, Ago, Sep · último pago: junio 2026"),
    ]},
]

# Nombres viejos que siguen andando si alguien los dejó escritos (no se ofrecen).
ALIAS = {"por_vencer": "activos", "cantidad_por_vencer": "cantidad_activos",
         "total_por_vencer": "total_activos", "lista_por_vencer": "lista_activos"}

CUOTA_SOCIO = {"meses_debe", "meses_impagos", "ultimo_mes_pago"}
CUOTA_INTERNO = {"total_deudores_cuota", "lista_cuotas"}

_ETIQUETA = re.compile(r"{{\s*(\w+)\s*}}")


def nombres(juego: list[dict]) -> list[str]:
    return [k for g in juego for k, *_ in g["items"]]


def usadas(*textos: str | None) -> set[str]:
    """Qué etiquetas aparecen en estos textos (tolera espacios: {{ nombre }})."""
    return {m for t in textos if t for m in _ETIQUETA.findall(t)}


def catalogo() -> dict:
    """Para la pantalla: los chips y la ayuda de cada juego."""
    def armar(juego):
        return [{"grupo": g["grupo"], "items": [{"etiqueta": k, "descripcion": d, "ejemplo": e}
                                                for k, d, e in g["items"]]}
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
