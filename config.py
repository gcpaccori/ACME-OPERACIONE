import os
from pydantic_settings import BaseSettings
from pydantic import ConfigDict

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

settings = Settings()
