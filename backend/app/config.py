"""Configuración de la app, cargada desde variables de entorno / .env."""
from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Koha (datos reales)
    koha_base_url: str = "http://3169.bepe.ar:8080"
    # Credenciales SOLO para scripts/probe_koha.py (la app usa las de cada bibliotecaria).
    koha_user: str = ""
    koha_password: str = ""

    # IDs de reportes guardados en Koha (None hasta que se creen)
    report_member_search_id: int | None = None
    report_member_loans_id: int | None = None
    report_loans_active_id: int | None = None
    report_loans_overdue_id: int | None = None
    report_member_profile_id: int | None = None
    report_member_account_id: int | None = None
    report_member_history_id: int | None = None
    report_loans_contact_id: int | None = None

    # Auth de la app (las bibliotecarias entran con sus credenciales de Koha;
    # esta clave solo firma el token de sesión que emite la app).
    app_secret_key: str = "dev-insecure-secret"
    app_token_expire_minutes: int = 480

    # CORS
    cors_origins: str = "http://localhost:5173"

    # ── Envío de mails (SMTP) ───────────────────────────────────────────────
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""               # remitente (ej: biblioteca@dominio.org)
    smtp_from_name: str = "Biblioteca Popular Osvaldo Bayer"
    smtp_use_tls: bool = True         # STARTTLS (puerto 587)
    # Seguridad: si está en True, NO envía de verdad (simula). Pasar a False para enviar.
    mail_dry_run: bool = True

    @field_validator(
        "report_member_search_id",
        "report_member_loans_id",
        "report_loans_active_id",
        "report_loans_overdue_id",
        "report_member_profile_id",
        "report_member_account_id",
        "report_member_history_id",
        "report_loans_contact_id",
        mode="before",
    )
    @classmethod
    def _empty_to_none(cls, v):
        """Un REPORT_*_ID vacío en .env vale como 'no configurado' (None)."""
        if v is None or (isinstance(v, str) and v.strip() == ""):
            return None
        return v

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
