"""Genera el PDF de la Factura C con el QR oficial de ARCA.

EN CONSTRUCCIÓN: igual que `arca.py`, todavía no se usa desde la app (no hay
endpoint ni pantalla). El PDF ya se genera y los tests lo verifican, pero el
formato final hay que compararlo contra una factura real antes de usarlo.

El WSFEv1 solo autoriza los totales y devuelve el CAE; el comprobante impreso lo
armamos nosotros. Este módulo reproduce el formato de la factura de la biblioteca
(ver modelo en Descargas) e incluye el **código QR** obligatorio (RG 4892): una URL
``https://www.afip.gob.ar/fe/qr/?p=<base64(JSON)>`` que permite verificar el
comprobante en la web de ARCA.

Usa ``fpdf2`` (motor liviano, puro Python) y ``qrcode`` con backend PNG puro
(``pypng``), sin Pillow, para instalar limpio en Railway.
"""
from __future__ import annotations

import base64
import io
import json
from datetime import date, datetime

# Datos del emisor (tomados del modelo de factura de la biblioteca). Son fijos:
# si algún dato institucional cambiara, se edita acá.
EMISOR = {
    "razon_social": "ASOCIACION BIBLIOTECA POPULAR OSVALDO BAYER",
    "domicilio": "Los Maquis 33 - Villa La Angostura, Neuquén",
    "cond_iva": "IVA Sujeto Exento",
    "cuit": "30675834306",
    "iibb": "000-147954/07",
    "inicio_actividades": "27/05/1992",
}

_QR_BASE = "https://www.afip.gob.ar/fe/qr/?p="


# ── Fechas ──────────────────────────────────────────────────────────────────
def _to_date(v: date | str) -> date:
    """Acepta date, 'YYYYMMDD' o 'YYYY-MM-DD' y devuelve un date."""
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if "-" in s:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    return datetime.strptime(s, "%Y%m%d").date()


def _ar(v: date | str) -> str:
    """DD/MM/AAAA para mostrar."""
    return _to_date(v).strftime("%d/%m/%Y")


def _iso(v: date | str) -> str:
    """YYYY-MM-DD (para el QR)."""
    return _to_date(v).strftime("%Y-%m-%d")


# ── QR oficial de ARCA (RG 4892) ────────────────────────────────────────────
def qr_url(
    *,
    fecha: date | str,
    cuit: str | int,
    pto_vta: int,
    tipo_cmp: int,
    nro_cmp: int,
    importe: float,
    cae: str | int,
    moneda: str = "PES",
    ctz: float = 1,
    tipo_doc_rec: int | None = None,
    nro_doc_rec: str | int | None = None,
) -> str:
    """Construye la URL del QR de verificación de ARCA."""
    data: dict = {
        "ver": 1,
        "fecha": _iso(fecha),
        "cuit": int(cuit),
        "ptoVta": int(pto_vta),
        "tipoCmp": int(tipo_cmp),
        "nroCmp": int(nro_cmp),
        "importe": round(float(importe), 2),
        "moneda": moneda,
        "ctz": int(ctz) if float(ctz).is_integer() else float(ctz),
    }
    if tipo_doc_rec:
        data["tipoDocRec"] = int(tipo_doc_rec)
    if nro_doc_rec:
        data["nroDocRec"] = int(nro_doc_rec)
    data["tipoCodAut"] = "E"
    data["codAut"] = int(cae)
    raw = base64.b64encode(json.dumps(data, separators=(",", ":")).encode("utf-8")).decode("ascii")
    return _QR_BASE + raw


def _qr_png(url: str) -> bytes:
    import qrcode
    from qrcode.image.pure import PyPNGImage

    img = qrcode.make(url, image_factory=PyPNGImage, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue()


# ── PDF ─────────────────────────────────────────────────────────────────────
def _money(n: float) -> str:
    """Formato $ argentino: 9.000,00"""
    s = f"{float(n):,.2f}"
    return s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def factura_c_pdf(
    *,
    pto_vta: int,
    nro: int,
    fecha: date | str,
    cae: str | int,
    cae_vto: date | str,
    importe: float,
    receptor: dict,
    items: list[dict],
    periodo_desde: date | str | None = None,
    periodo_hasta: date | str | None = None,
    vto_pago: date | str | None = None,
    cond_venta: str = "Contado",
    copia: str = "ORIGINAL",
) -> bytes:
    """Devuelve los bytes del PDF de una Factura C.

    receptor: {"doc_tipo":96,"doc_nro":...,"nombre":...,"cond_iva":"Consumidor Final","domicilio":""}
    items:    [{"codigo":"","descripcion":"cuotas ...","cantidad":1,"u_medida":"unidades","precio":9000.0,"bonif_pct":0}]
    """
    from fpdf import FPDF
    from fpdf.enums import XPos, YPos

    # Equivalentes modernos de los viejos ln=1 / ln=2 (evita DeprecationWarning).
    LN = dict(new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    LN2 = dict(new_x=XPos.LEFT, new_y=YPos.NEXT)

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=False)
    pdf.add_page()
    pdf.set_margins(10, 10, 10)
    W = 190  # ancho útil (210 - 2*10)
    x0 = 10

    def line(y):
        pdf.line(x0, y, x0 + W, y)

    # ── ORIGINAL/COPIA centrado ──
    pdf.set_xy(x0, 10)
    pdf.set_font("helvetica", "B", 9)
    pdf.cell(W, 5, copia, align="C")

    # ── Encabezado: caja con emisor (izq) | letra C (centro) | factura (der) ──
    top = 16
    box_h = 34
    mid = x0 + W / 2
    pdf.rect(x0, top, W, box_h)
    pdf.line(mid, top, mid, top + box_h)          # divisor vertical central
    # Cuadrito de la letra "C" a caballo del divisor
    lb_w, lb_h = 16, 16
    pdf.rect(mid - lb_w / 2, top, lb_w, lb_h)
    pdf.set_xy(mid - lb_w / 2, top + 1)
    pdf.set_font("helvetica", "B", 22)
    pdf.cell(lb_w, 10, "C", align="C")
    pdf.set_xy(mid - lb_w / 2, top + 11)
    pdf.set_font("helvetica", "", 7)
    pdf.cell(lb_w, 4, "COD. 011", align="C")

    # Izquierda: datos del emisor
    lx = x0 + 3
    pdf.set_xy(lx, top + 2)
    pdf.set_font("helvetica", "B", 10)
    pdf.multi_cell(W / 2 - 12, 4, EMISOR["razon_social"])
    pdf.set_xy(lx, top + 12)
    pdf.set_font("helvetica", "", 7.5)

    def field(label, value, w_label=32):
        pdf.set_font("helvetica", "B", 7.5)
        pdf.cell(w_label, 4, label)
        pdf.set_font("helvetica", "", 7.5)
        pdf.cell(0, 4, value, **LN)
        pdf.set_x(lx)

    field("Razón Social:", "")
    field("Domicilio Comercial:", EMISOR["domicilio"])
    field("Condición frente al IVA:", EMISOR["cond_iva"])

    # Derecha: FACTURA + numeración + datos fiscales (a la derecha del cuadro de la "C")
    rx = mid + 10
    pdf.set_xy(rx, top + 2)
    pdf.set_font("helvetica", "B", 15)
    pdf.cell(0, 7, "FACTURA", **LN)
    pdf.set_font("helvetica", "", 8)

    def rfield(label, value):
        pdf.set_x(rx)
        pdf.set_font("helvetica", "B", 8)
        pdf.cell(40, 4.5, label)
        pdf.set_font("helvetica", "", 8)
        pdf.cell(0, 4.5, value, **LN)

    pdf.set_xy(rx, top + 10)
    rfield("Punto de Venta:", f"{int(pto_vta):04d}    Comp. Nro: {int(nro):08d}")
    rfield("Fecha de Emisión:", _ar(fecha))
    rfield("CUIT:", EMISOR["cuit"])
    rfield("Ingresos Brutos:", EMISOR["iibb"])
    rfield("Inicio de Actividades:", EMISOR["inicio_actividades"])

    # ── Período facturado ──
    y = top + box_h
    ph = 7
    pdf.rect(x0, y, W, ph)
    pdf.set_xy(x0 + 3, y + 1.5)
    pdf.set_font("helvetica", "B", 8)
    pd = _ar(periodo_desde) if periodo_desde else _ar(fecha)
    ph_ = _ar(periodo_hasta) if periodo_hasta else _ar(fecha)
    vp = _ar(vto_pago) if vto_pago else _ar(fecha)
    pdf.cell(48, 4, "Período Facturado Desde: ")
    pdf.set_font("helvetica", "", 8)
    pdf.cell(22, 4, pd)
    pdf.set_font("helvetica", "B", 8)
    pdf.cell(14, 4, "Hasta: ")
    pdf.set_font("helvetica", "", 8)
    pdf.cell(22, 4, ph_)
    pdf.set_font("helvetica", "B", 8)
    pdf.cell(40, 4, "Vto. para el pago: ")
    pdf.set_font("helvetica", "", 8)
    pdf.cell(0, 4, vp)

    # ── Receptor ──
    y += ph
    rh = 16
    pdf.rect(x0, y, W, rh)
    pdf.set_xy(x0 + 3, y + 1.5)
    doc_tipo = int(receptor.get("doc_tipo") or 99)
    doc_label = {80: "CUIT", 86: "CUIL", 96: "DNI"}.get(doc_tipo, "DNI")
    pdf.set_font("helvetica", "B", 8)
    pdf.cell(20, 4.5, f"{doc_label}:")
    pdf.set_font("helvetica", "", 8)
    pdf.cell(45, 4.5, str(receptor.get("doc_nro") or ""))
    pdf.set_font("helvetica", "B", 8)
    pdf.cell(45, 4.5, "Apellido y Nombre / Razón Social:")
    pdf.set_font("helvetica", "", 8)
    pdf.cell(0, 4.5, str(receptor.get("nombre") or ""), **LN)
    pdf.set_x(x0 + 3)
    pdf.set_font("helvetica", "B", 8)
    pdf.cell(38, 4.5, "Condición frente al IVA:")
    pdf.set_font("helvetica", "", 8)
    pdf.cell(0, 4.5, str(receptor.get("cond_iva") or "Consumidor Final"), **LN)
    pdf.set_x(x0 + 3)
    pdf.set_font("helvetica", "B", 8)
    pdf.cell(28, 4.5, "Condición de venta:")
    pdf.set_font("helvetica", "", 8)
    pdf.cell(0, 4.5, cond_venta, **LN)

    # ── Tabla de ítems ──
    y += rh + 1
    # Encabezado gris
    cols = [
        ("Código", 18, "L"),
        ("Producto / Servicio", 74, "L"),
        ("Cantidad", 18, "R"),
        ("U. Medida", 20, "C"),
        ("Precio Unit.", 24, "R"),
        ("Subtotal", 36, "R"),
    ]
    pdf.set_fill_color(225, 225, 225)
    pdf.set_draw_color(150, 150, 150)
    pdf.set_xy(x0, y)
    pdf.set_font("helvetica", "B", 7.5)
    for name, w, al in cols:
        pdf.cell(w, 6, name, border="B", align=al, fill=True)
    pdf.ln(6)
    pdf.set_font("helvetica", "", 8)
    for it in items:
        cant = float(it.get("cantidad") or 1)
        precio = float(it.get("precio") or 0)
        sub = cant * precio
        row = [
            (str(it.get("codigo") or ""), 18, "L"),
            (str(it.get("descripcion") or ""), 74, "L"),
            (f"{cant:.2f}".rstrip("0").rstrip("."), 18, "R"),
            (str(it.get("u_medida") or "unidades"), 20, "C"),
            (_money(precio), 24, "R"),
            (_money(sub), 36, "R"),
        ]
        pdf.set_x(x0)
        for val, w, al in row:
            pdf.cell(w, 6, val, align=al)
        pdf.ln(6)

    # ── Totales (abajo a la derecha) ──
    ty = 232
    line(ty)
    pdf.set_xy(x0, ty + 3)

    def total_row(label, value, bold=False):
        pdf.set_x(x0)
        pdf.set_font("helvetica", "B" if bold else "", 9 if not bold else 10)
        pdf.cell(W - 45, 6, label, align="R")
        pdf.cell(45, 6, f"$ {_money(value)}", align="R", **LN)

    otros_trib = 0.0
    total_row("Subtotal: $", importe)
    total_row("Importe Otros Tributos: $", otros_trib)
    total_row("Importe Total: $", importe, bold=True)

    # ── Pie: QR + CAE ──
    py = 262
    line(py)
    url = qr_url(
        fecha=fecha, cuit=EMISOR["cuit"], pto_vta=pto_vta, tipo_cmp=11,
        nro_cmp=nro, importe=importe, cae=cae,
        tipo_doc_rec=receptor.get("doc_tipo"), nro_doc_rec=receptor.get("doc_nro"),
    )
    png = _qr_png(url)
    pdf.image(io.BytesIO(png), x=x0 + 2, y=py + 3, w=26)
    pdf.set_xy(x0 + 32, py + 6)
    pdf.set_font("helvetica", "B", 11)
    pdf.cell(30, 6, "ARCA", **LN2)
    pdf.set_font("helvetica", "", 6.5)
    pdf.set_x(x0 + 32)
    pdf.cell(60, 3, "Agencia de Recaudación y Control Aduanero")
    pdf.set_xy(x0 + 32, py + 16)
    pdf.set_font("helvetica", "BI", 8)
    pdf.cell(0, 4, "Comprobante Autorizado")

    pdf.set_xy(x0 + W - 70, py + 6)
    pdf.set_font("helvetica", "B", 9)
    pdf.cell(70, 5, f"CAE N°:  {cae}", align="R", **LN2)
    pdf.set_x(x0 + W - 70)
    pdf.cell(70, 5, f"Fecha de Vto. de CAE:  {_ar(cae_vto)}", align="R")

    return bytes(pdf.output())
