import os
from pydantic_settings import BaseSettings
from pydantic import ConfigDict, field_validator

class Settings(BaseSettings):
    model_config = ConfigDict(env_file=".env", case_sensitive=False, extra="ignore")
    
    # Supabase
    supabase_url: str = os.getenv("SUPABASE_URL", "")
    supabase_key: str = os.getenv("SUPABASE_ANON_KEY", "")
    supabase_anon_jwt: str = os.getenv("VITE_SUPABASE_ANON_KEY", os.getenv("SUPABASE_JWT_ANON_KEY", ""))
    supabase_service_role_key: str = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    database_url: str = os.getenv("SUPABASE_DB_URL", os.getenv("DATABASE_URL", "sqlite:///./acme.db"))
    
    # Culqi
    culqi_public_key: str = os.getenv("CULQI_PUBLIC_KEY", "")
    culqi_private_key: str = os.getenv("CULQI_PRIVATE_KEY", "")
    
    # API
    api_host: str = os.getenv("API_HOST", "0.0.0.0")
    api_port: int = int(os.getenv("API_PORT", "8000"))
    debug: bool = os.getenv("DEBUG", "False").lower() == "true"
    frontend_url: str = os.getenv("FRONTEND_URL", "http://localhost:3000")
    frontend_urls: str = os.getenv("FRONTEND_URLS", "")
    skip_legacy_db_init: bool = os.getenv("SKIP_LEGACY_DB_INIT", "False").lower() == "true"
    routing_api_url: str = os.getenv("ROUTING_API_URL", "https://router.project-osrm.org")
    routing_timeout_seconds: float = float(os.getenv("ROUTING_TIMEOUT_SECONDS", "4.0"))
    geocoding_api_url: str = os.getenv("GEOCODING_API_URL", "https://nominatim.openstreetmap.org/reverse")
    geocoding_search_api_url: str = os.getenv("GEOCODING_SEARCH_API_URL", "https://nominatim.openstreetmap.org/search")
    geocoding_user_agent: str = os.getenv("GEOCODING_USER_AGENT", "ACME-Courier-Huancavelica/1.0")
    geocoding_timeout_seconds: float = float(os.getenv("GEOCODING_TIMEOUT_SECONDS", "5.0"))
    platform_service_fee_rate: float = float(os.getenv("PLATFORM_SERVICE_FEE_RATE", "0.036"))
    igv_rate: float = float(os.getenv("IGV_RATE", "0.18"))
    culqi_online_rate: float = float(os.getenv("CULQI_ONLINE_RATE", "0.0344"))
    culqi_online_fixed_usd: float = float(os.getenv("CULQI_ONLINE_FIXED_USD", "0.20"))
    culqi_exchange_rate: float = float(os.getenv("CULQI_EXCHANGE_RATE", "3.85"))
    culqi_min_fee_pen: float = float(os.getenv("CULQI_MIN_FEE_PEN", "3.50"))
    culqi_min_threshold_pen: float = float(os.getenv("CULQI_MIN_THRESHOLD_PEN", "87.72"))
    pass_culqi_fee_to_customer: bool = os.getenv("PASS_CULQI_FEE_TO_CUSTOMER", "True").lower() == "true"

    @field_validator("debug", "skip_legacy_db_init", mode="before")
    @classmethod
    def parse_bool_like(cls, value):
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        normalized = str(value).strip().lower()
        if normalized in {"1", "true", "yes", "on", "debug", "development"}:
            return True
        if normalized in {"0", "false", "no", "off", "release", "production", "prod", ""}:
            return False
        return value

settings = Settings()
