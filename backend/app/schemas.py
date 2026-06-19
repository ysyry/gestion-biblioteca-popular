"""Esquemas Pydantic de entrada/salida de la API."""
from __future__ import annotations

from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str


class MailRecipient(BaseModel):
    email: str | None = None
    vars: dict[str, str] = {}        # nombre, apellido, carnet, etc.
    subject: str | None = None       # override individual (opcional)
    body: str | None = None          # override individual (opcional)


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
