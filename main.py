from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from config import settings
import routes_courier_payments
import routes_courier_quote
import routes_courier_orders
import routes_webhooks
import routes_admin_refunds
import logging

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def allowed_origins() -> list[str]:
    origins = [
        settings.frontend_url,
        "http://localhost:3000",
        "http://localhost:5173",
    ]
    origins.extend(origin.strip() for origin in settings.frontend_urls.split(",") if origin.strip())
    return list(dict.fromkeys(origin for origin in origins if origin))

# Crear aplicación FastAPI
app = FastAPI(
    title="ACME Courier Payments API",
    description="API de pedidos y pagos Culqi del courier ACME",
    version="1.0.0"
)

# Configurar CORS para permitir peticiones desde tu página web
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins(),
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Incluir rutas
app.include_router(routes_courier_payments.router)
app.include_router(routes_courier_quote.router)
app.include_router(routes_courier_orders.router)
app.include_router(routes_webhooks.router)
app.include_router(routes_admin_refunds.router)

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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.debug
    )
