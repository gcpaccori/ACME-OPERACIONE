from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from config import settings
from database import init_db
import routes_products
import routes_orders
import routes_payments
import logging

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Crear aplicación FastAPI
app = FastAPI(
    title="ACME Ecommerce API",
    description="API para procesamiento de compras con integración Culqi",
    version="1.0.0"
)

# Configurar CORS para permitir peticiones desde tu página web
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url, "http://localhost:3000", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Incluir rutas
app.include_router(routes_products.router)
app.include_router(routes_orders.router)
app.include_router(routes_payments.router)

# Health check
@app.get("/health")
def health_check():
    """Verificar que la API está funcionando"""
    return {"status": "ok"}

# Raíz
@app.get("/")
def root():
    """Endpoint raíz"""
    return {
        "mensaje": "Bienvenido a ACME Ecommerce API",
        "docs": "/docs",
        "redoc": "/redoc"
    }

# Inicializar BD al arrancar
@app.on_event("startup")
async def startup():
    """Crear tablas si no existen"""
    try:
        init_db()
        logger.info("✅ Base de datos inicializada correctamente")
    except Exception as e:
        logger.error(f"❌ Error inicializando BD: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.debug
    )