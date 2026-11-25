from functools import lru_cache
from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _normalize_database_url(value: str) -> str:
    """Render supplies ``postgres://`` URLs; pin them to psycopg 3 explicitly."""
    if value.startswith("postgres://"):
        return "postgresql+psycopg://" + value[len("postgres://") :]
    if value.startswith("postgresql://"):
        return "postgresql+psycopg://" + value[len("postgresql://") :]
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # Application
    app_name: str = "ESSVEE Payment Manager"
    company_name: str = "ESSVEE Associates"
    environment: str = "development"
    secret_key: str = "dev-only-secret-change-me-before-production"
    access_token_expire_minutes: int = 60 * 12
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Database
    database_url: str = "postgresql+psycopg://essvee:essvee@localhost:5432/essvee"
    db_echo: bool = False

    # Bootstrap administrator (used only when the users table is empty)
    admin_email: str = "admin@essvee.in"
    admin_password: str = "ChangeMe123!"

    # Integrations (defaults are empty on purpose: no credentials are assumed)
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""
    razorpay_mode: str = "test"
    razorpay_callback_url: str = ""

    # UPI deep links (used instead of Razorpay when payment_provider=UPI)
    payment_provider: str = "UPI"
    upi_vpa: str = ""
    upi_payee_name: str = ""
    public_base_url: str = "http://localhost:8000"
    upi_amount_mode: str = "CUSTOMER_ENTERS"

    whatsapp_access_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_waba_id: str = ""
    whatsapp_webhook_verify_token: str = ""
    whatsapp_app_secret: str = ""
    whatsapp_api_version: str = "v21.0"

    # Google Sheets (the service account JSON is a secret; the spreadsheet ID is not)
    google_service_account_json: str = ""
    google_spreadsheet_id: str = ""

    # Automation
    enable_scheduler: bool = True
    timezone: str = "Asia/Kolkata"

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, value):
        return value if not isinstance(value, str) else _normalize_database_url(value)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def split_origins(cls, value):
        return value

    @property
    def cors_origin_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in ("production", "prod")


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
