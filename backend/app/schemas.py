"""Esquemas Pydantic de entrada/salida de la API."""
from __future__ import annotations

from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    """Lo que devuelve el login: el token y el perfil de quien entró.

    El frontend arma el menú con `secciones` y decide qué mostrar con `permisos`,
    así nadie ve una pestaña que después le daría 403.
    """
    access_token: str
    token_type: str = "bearer"
    username: str
    nombre: str = ""
    rol: str = ""
    rol_etiqueta: str = ""
    subcomision: str = ""
    permisos: list[str] = []
    secciones: list[dict] = []
    menu: list[dict] = []


class MailRecipient(BaseModel):
    email: str | None = None
    vars: dict[str, str] = {}        # nombre, apellido, carnet, etc.
    subject: str | None = None       # override individual (opcional)
    body: str | None = None          # override individual (opcional)
    # Libros del socio por categoría ({"vencidos"|"por_vencer"|"prestamos": [{"titulo","fecha"}]}).
    # Si vienen, el backend arma la tabla HTML unificada (igual que los automáticos).
    loans: dict[str, list[dict]] | None = None


class MailSendRequest(BaseModel):
    subject: str                     # plantilla del asunto (con {{variables}})
    body: str                        # plantilla del cuerpo
    recipients: list[MailRecipient]
    dry_run: bool | None = None      # None = usa el default del .env (MAIL_DRY_RUN)
    test_to: str | None = None       # si se setea, manda todo a esa dirección (prueba)


class Member(BaseModel):
    cardnumber: str | None = None
    surname: str | None = None
    firstname: str | None = None
    email: str | None = None
    phone: str | None = None
    category: str | None = None
    dateexpiry: str | None = None


class MemberLoan(BaseModel):
    barcode: str | None = None
    title: str | None = None
    author: str | None = None
    issuedate: str | None = None
    date_due: str | None = None


class ActiveLoan(BaseModel):
    cardnumber: str | None = None
    surname: str | None = None
    firstname: str | None = None
    barcode: str | None = None
    title: str | None = None
    issuedate: str | None = None
    date_due: str | None = None


class OverdueLoan(BaseModel):
    cardnumber: str | None = None
    surname: str | None = None
    firstname: str | None = None
    phone: str | None = None
    email: str | None = None
    barcode: str | None = None
    title: str | None = None
    date_due: str | None = None
    dias_atraso: int | None = None
