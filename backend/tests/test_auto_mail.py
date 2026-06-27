"""Tests de los reportes automáticos: split de préstamos y exclusión de bajas/becados."""
from app import auto_mail


def test_split_loans():
    rows = [{"dias_atraso": "5"}, {"dias_atraso": "-2"}, {"dias_atraso": "-30"}, {"dias_atraso": None}]
    venc, porv = auto_mail._split_loans(rows, dias_antes=3, umbral_atraso=1)
    assert len(venc) == 1            # solo el de +5
    assert len(porv) == 1            # solo el de -2 (dentro de 3 días); -30 queda fuera


async def test_socios_excluye_bajas_y_becados(monkeypatch):
    async def loans():
        return [
            {"cardnumber": "1", "surname": "Activo", "firstname": "A", "email": "a@x.com", "dias_atraso": "10"},
            {"cardnumber": "2", "surname": "Baja", "firstname": "B", "email": "b@x.com", "dias_atraso": "10"},
        ]
    async def members():
        return {"1": {"surname": "Activo", "firstname": "A", "email": "a@x.com", "categorycode": "AD"},
                "2": {"surname": "Baja", "firstname": "B", "email": "b@x.com", "categorycode": "B"},
                "3": {"surname": "Becado", "firstname": "C", "email": "c@x.com", "categorycode": "BEC."}}
    async def cmap():
        return {"3": {"matricula": "3", "apellido": "Becado", "nombre": "C", "debe": 5, "impagos": ["Ene"]}}
    monkeypatch.setattr(auto_mail, "_all_loans", loans)
    monkeypatch.setattr(auto_mail, "_members_map", members)
    monkeypatch.setattr(auto_mail, "_cuota_map", cmap)

    rep = {"tipo": "socios", "dias_antes": 3, "umbral_atraso": 1,
           "incluir_vencidos": True, "incluir_por_vencer": False,
           "incluir_cuotas": True, "umbral_cuota": 1, "excluidos": []}
    data = await auto_mail._socios_recipients(rep)
    carnets = {r["_carnet"] for r in data["recipients"]}
    assert "1" in carnets            # activo con vencidos: entra
    assert "2" not in carnets        # de baja: NO recibe
    assert "3" not in carnets        # becado: no cuenta como deuda de cuota


async def test_socios_respeta_excluidos(monkeypatch):
    async def loans():
        return [{"cardnumber": "1", "surname": "X", "firstname": "Y", "email": "a@x.com", "dias_atraso": "10"}]
    async def members():
        return {"1": {"categorycode": "AD", "email": "a@x.com", "surname": "X", "firstname": "Y"}}
    async def cmap():
        return {}
    monkeypatch.setattr(auto_mail, "_all_loans", loans)
    monkeypatch.setattr(auto_mail, "_members_map", members)
    monkeypatch.setattr(auto_mail, "_cuota_map", cmap)
    rep = {"tipo": "socios", "dias_antes": 3, "umbral_atraso": 1,
           "incluir_vencidos": True, "incluir_por_vencer": False,
           "incluir_cuotas": False, "excluidos": ["1"]}
    data = await auto_mail._socios_recipients(rep)
    assert data["recipients"] == []   # el único candidato está excluido
