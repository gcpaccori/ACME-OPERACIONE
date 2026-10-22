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

    # Izipay (API REST V4 con formulario incrustado). Las claves salen del Back
    # Office Vendedor > Configuracion > Tienda > Claves de API REST. El modo
    # (TEST o PRODUCTION) lo decide la contrasena que se ponga: la de test
    # empieza con "testpassword_" y la de produccion con "prodpassword_".
    izipay_username: str = os.getenv("IZIPAY_USERNAME", "")
    izipay_password: str = os.getenv("IZIPAY_PASSWORD", "")
    izipay_public_key: str = os.getenv("IZIPAY_PUBLIC_KEY", "")
    izipay_hmac_key: str = os.getenv("IZIPAY_HMAC_KEY", "")
    izipay_api_url: str = os.getenv("IZIPAY_API_URL", "https://api.micuentaweb.pe")
    # Monto minimo por pedido en centimos. Izipay no impone el minimo de S/ 6
    # de las ordenes de Culqi; se deja S/ 1 para no cobrar pedidos en cero.
    izipay_min_order_centimos: int = int(os.getenv("IZIPAY_MIN_ORDER_CENTIMOS", "100"))
    # Pagina publica de demostracion del boton de Izipay (/pago-izipay en la
    # web), para que Izipay valide el sitio sin cambiar la pasarela del carrito.
    # Cobra un monto fijo que no corresponde a ningun pedido.
    # Con las claves de TEST, cualquiera puede "pagar" con las tarjetas de
    # prueba publicas de Izipay. En produccion esos pagos no deben liberar el
    # pedido al negocio; solo se aceptan si esto esta activo (entornos de prueba).
    izipay_accept_test_payments: bool = os.getenv("IZIPAY_ACCEPT_TEST_PAYMENTS", "").strip().lower() in ("1", "true", "yes")
    izipay_demo_enabled: bool = os.getenv("IZIPAY_DEMO_ENABLED", "").strip().lower() in ("1", "true", "yes")
    izipay_demo_amount_centimos: int = int(os.getenv("IZIPAY_DEMO_AMOUNT_CENTIMOS", "100"))

    # SDK de Izipay (boton de pagos de Punto Web): tarjeta, Yape, Plin y QR.
    # Las claves salen del panel de comercio (sandbox-panel.izipay.pe para
    # pruebas, panel.izipay.pe en produccion) > Desarrolladores > Claves.
    izipay_sdk_merchant_code: str = os.getenv("IZIPAY_SDK_MERCHANT_CODE", "")
    # "Clave API del nuevo boton de pagos": solo sirve para pedir el token de sesion.
    izipay_sdk_api_key: str = os.getenv("IZIPAY_SDK_API_KEY", "")
    # "Clave Hash": firma las respuestas del checkout y de la IPN.
    izipay_sdk_hash_key: str = os.getenv("IZIPAY_SDK_HASH_KEY", "")
    # "Clave publica IZIPAY" (RSA): la usa el checkout en el navegador.
    izipay_sdk_rsa_public_key: str = os.getenv("IZIPAY_SDK_RSA_PUBLIC_KEY", "")
    # "sandbox" o "production".
    izipay_sdk_env: str = os.getenv("IZIPAY_SDK_ENV", "sandbox")
    # URL publica de /api/webhooks/izipay-sdk; Izipay la recibe en cada pago.
    izipay_sdk_ipn_url: str = os.getenv("IZIPAY_SDK_IPN_URL", "")
    # RUC del comercio: Izipay lo pide para las devoluciones.
    izipay_sdk_ruc: str = os.getenv("IZIPAY_SDK_RUC", "")
    izipay_sdk_pay_methods: str = os.getenv("IZIPAY_SDK_PAY_METHODS", "CARD,YAPE_CODE,PAGO_PUSH,QR")
    # Izipay exige direccion de facturacion; los pedidos no la guardan.
    izipay_sdk_billing_street: str = os.getenv("IZIPAY_SDK_BILLING_STREET", "Jr Virrey Toledo 100")
    izipay_sdk_billing_city: str = os.getenv("IZIPAY_SDK_BILLING_CITY", "Huancavelica")
    izipay_sdk_billing_state: str = os.getenv("IZIPAY_SDK_BILLING_STATE", "Huancavelica")
    izipay_sdk_billing_postal_code: str = os.getenv("IZIPAY_SDK_BILLING_POSTAL_CODE", "09001")

    # Pasarela activa: "izipay_sdk", "izipay" o "culqi". Si no se fija, se usa
    # la primera que tenga sus claves configuradas en ese orden, y Culqi si
    # ninguna las tiene: desplegar sin las variables nuevas no rompe los cobros.
    payment_provider: str = os.getenv("PAYMENT_PROVIDER", "")
    # Comision de Izipay por tarjeta nacional, sin IGV (el IGV se suma aparte).
    # Tarifa publicada en sus terminos: 3.44% + IGV y S/ 0.69 + IGV fijo por
    # operacion (filtros de seguridad). Ajustar si el contrato dice otra cosa.
    izipay_card_rate: float = float(os.getenv("IZIPAY_CARD_RATE", "0.0344"))
    izipay_card_fixed_pen: float = float(os.getenv("IZIPAY_CARD_FIXED_PEN", "0.69"))
    # Yape cobrado por Culqi, segun culqi.com/tarifas: desde 3.44%, comision
    # minima de S/ 3.50 para montos menores a S/ 87.72 y "comisiones inafectas
    # a IGV", por eso no se le suma IGV salvo que el contrato diga lo contrario.
    culqi_yape_rate: float = float(os.getenv("CULQI_YAPE_RATE", "0.0344"))
    culqi_yape_min_fee_pen: float = float(os.getenv("CULQI_YAPE_MIN_FEE_PEN", "3.50"))
    # Recargo al envio por cada local extra en un mismo pedido: el segundo local
    # suma 10% del envio, el tercero 15%, el cuarto 20% y el quinto 25%. El
    # numero de valores fija cuantos locales extra se aceptan.
    multi_store_delivery_surcharges: str = os.getenv("MULTI_STORE_DELIVERY_SURCHARGES", "0.10,0.15,0.20,0.25")
    culqi_yape_apply_igv: bool = os.getenv("CULQI_YAPE_APPLY_IGV", "false").lower() == "true"
    
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
    # Tarifa de servicio sobre el precio de los productos: 12% hasta S/ 100 y,
    # si pasan de S/ 100, 8% sobre todo el monto (regla del negocio: un pedido
    # de S/ 101 paga S/ 8.08, menos que uno de S/ 100).
    service_fee_tier_threshold_pen: float = float(os.getenv("SERVICE_FEE_TIER_THRESHOLD_PEN", "100"))
    service_fee_rate_up_to_threshold: float = float(os.getenv("SERVICE_FEE_RATE_UP_TO_THRESHOLD", "0.12"))
    service_fee_rate_above_threshold: float = float(os.getenv("SERVICE_FEE_RATE_ABOVE_THRESHOLD", "0.08"))
    # Categorias que ACME distribuye directamente: sus productos no suman a la
    # base de la tarifa de servicio, asi que no pagan ese adicional. El cliente
    # igual paga el precio del producto, el envio y la pasarela; solo se descuenta
    # de ellos la tarifa de servicio. Hoy es la categoria "Bebidas ACME", que esta
    # en las 18 tiendas; los demas rubros de bebida (Gaseosas, Jugos, etc.) son del
    # restaurante y si pagan tarifa. Nombres separados por coma, sin distinguir
    # mayusculas ni tildes.
    service_fee_exempt_categories: str = os.getenv("SERVICE_FEE_EXEMPT_CATEGORIES", "Bebidas ACME")
    igv_rate: float = float(os.getenv("IGV_RATE", "0.18"))
    culqi_online_rate: float = float(os.getenv("CULQI_ONLINE_RATE", "0.0344"))
    culqi_online_fixed_usd: float = float(os.getenv("CULQI_ONLINE_FIXED_USD", "0.20"))
    culqi_exchange_rate: float = float(os.getenv("CULQI_EXCHANGE_RATE", "3.85"))
    # La comision de la pasarela la paga el cliente en su propia linea
    # ("Comision de pasarela"), calculada segun el medio de pago elegido.

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


def active_payment_provider() -> str:
    """Pasarela con la que se abren los pagos nuevos."""
    explicit = str(settings.payment_provider or "").strip().lower()
    if explicit in ("izipay_sdk", "izipay", "culqi"):
        return explicit
    if (
        settings.izipay_sdk_merchant_code
        and settings.izipay_sdk_api_key
        and settings.izipay_sdk_hash_key
        and settings.izipay_sdk_rsa_public_key
    ):
        return "izipay_sdk"
    if settings.izipay_username and settings.izipay_password and settings.izipay_public_key:
        return "izipay"
    return "culqi"


def culqi_configured() -> bool:
    return bool(settings.culqi_public_key and settings.culqi_private_key)


def payment_provider_for(method: str | None) -> str:
    """Pasarela para un medio de pago concreto.

    Yape va por Culqi cuando la pasarela activa es el formulario de Izipay,
    que en esta tienda solo tiene contrato de tarjetas.
    """
    provider = active_payment_provider()
    if method == "yape" and provider == "izipay":
        if not culqi_configured():
            raise ValueError("El pago con Yape no esta disponible por ahora. Paga con tarjeta.")
        return "culqi"
    return provider


settings = Settings()
