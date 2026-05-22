from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from config import settings
from schemas import Base

# Crear motor de BD
engine = create_engine(
    settings.database_url,
    echo=settings.debug,
    pool_size=10,
    max_overflow=20
)

# Crear sesión
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db() -> Session:
    """Dependencia de FastAPI para obtener sesión de BD"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    """Crear todas las tablas en la BD"""
    Base.metadata.create_all(bind=engine)