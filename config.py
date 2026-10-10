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
    # Tarifa de servicio por tramos sobre el precio de los productos: 12% hasta
    # S/ 100 y 8% solo sobre lo que pasa de S/ 100. Asi un pedido de S/ 101
    # nunca paga menos tarifa que uno de S/ 100.
    service_fee_tier_threshold_pen: float = float(os.getenv("SERVICE_FEE_TIER_THRESHOLD_PEN", "100"))
    service_fee_rate_up_to_threshold: float = float(os.getenv("SERVICE_FEE_RATE_UP_TO_THRESHOLD", "0.12"))
    service_fee_rate_above_threshold: float = float(os.getenv("SERVICE_FEE_RATE_ABOVE_THRESHOLD", "0.08"))
    igv_rate: float = float(os.getenv("IGV_RATE", "0.18"))
    culqi_online_rate: float = float(os.getenv("CULQI_ONLINE_RATE", "0.0344"))
    culqi_online_fixed_usd: float = float(os.getenv("CULQI_ONLINE_FIXED_USD", "0.20"))
    culqi_exchange_rate: float = float(os.getenv("CULQI_EXCHANGE_RATE", "3.85"))
    culqi_min_fee_pen: float = float(os.getenv("CULQI_MIN_FEE_PEN", "3.50"))
    # Monto a partir del cual la comision por tasa supera al minimo. Se deja en
    # 0 para que se calcule sola a partir de la tasa, el fijo y el minimo: el
    # valor que estaba escrito a mano (87.72) no cuadraba con ninguno de los
    # dos y producia un salto en la comision justo en ese punto.
    culqi_min_threshold_pen: float = float(os.getenv("CULQI_MIN_THRESHOLD_PEN", "0"))
    # La comision de la tarjeta la paga el cliente, pero no como linea aparte:
    # se suma dentro de la "Tarifa de servicio" (ver routes_courier_quote.py).

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
