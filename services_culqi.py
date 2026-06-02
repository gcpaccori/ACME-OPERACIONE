from config import settings
from typing import Dict, Any
from datetime import datetime, timedelta
import requests
import logging
import re
import uuid

logger = logging.getLogger(__name__)

CULQI_API_BASE = "https://api.culqi.com/v2"
CULQI_MIN_ORDER_AMOUNT = 600
CULQI_MAX_ORDER_AMOUNT = 700000
CULQI_ORDER_DESCRIPTION_MAX_LENGTH = 80
CULQI_SANDBOX_YAPE_PHONE = "900000001"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _compact_text(value: str | None, fallback: str, max_length: int | None = None) -> str:
    text = " ".join(str(value or "").strip().split()) or fallback
    if max_length and len(text) > max_length:
        return text[:max_length].rstrip()
    return text


def _normalize_email(value: str | None) -> str:
    return str(value or "").strip().lower()


def _normalize_phone(value: str | None) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) == 9:
        return f"+51{digits}"
    if digits.startswith("51") and len(digits) == 11:
        return f"+{digits}"
    if 9 <= len(digits) <= 15:
        return f"+{digits}"
    return "+51999999999"


def _build_order_number(pedido_id: int | str) -> str:
    safe_reference = "".join(ch for ch in str(pedido_id) if ch.isalnum())[:8] or "pedido"
    return f"acme-{safe_reference}-{uuid.uuid4().hex[:12]}"


def _is_test_key(value: str | None) -> bool:
    return str(value or "").strip().startswith(("pk_test", "sk_test"))


def _culqi_error_message(data: Dict[str, Any]) -> str:
    merchant_message = data.get("merchant_message")
    user_message = data.get("user_message")
    param = data.get("param")
    if merchant_message:
        detail = str(merchant_message)
        if param:
            detail = f"{detail} Campo: {param}."
        return detail
    if user_message:
        return str(user_message)
    return "No se pudo crear la orden Culqi"

class CulqiService:
    """Servicio para procesar pagos con Culqi"""
    
    def __init__(self):
        self.public_key = settings.culqi_public_key
        self.private_key = settings.culqi_private_key

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.private_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _is_sandbox(self) -> bool:
        return _is_test_key(self.public_key) or _is_test_key(self.private_key)

    def _request(self, method: str, path: str, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
        response = requests.request(
            method,
            f"{CULQI_API_BASE}{path}",
            headers=self._headers(),
            json=payload,
            timeout=20,
        )
        try:
            data = response.json()
        except ValueError:
            data = {"merchant_message": response.text}
        return {"status": response.status_code, "data": data}

    def crear_orden_checkout(
        self,
        pedido_id: int | str,
        monto: int,
        email: str,
        nombre: str,
        telefono: str | None,
        descripcion: str,
    ) -> Dict[str, Any]:
        """Crear una orden Culqi para habilitar checkout multipago."""
        if monto < CULQI_MIN_ORDER_AMOUNT:
            return {
                "exito": False,
                "mensaje": "Culqi requiere un monto minimo de S/ 6.00 para ordenes multipago.",
                "respuesta_completa": None,
                "error_tipo": "validation",
            }
        if monto > CULQI_MAX_ORDER_AMOUNT:
            return {
                "exito": False,
                "mensaje": "El monto excede el limite permitido por Culqi para ordenes multipago.",
                "respuesta_completa": None,
                "error_tipo": "validation",
            }

        clean_email = _normalize_email(email)
        if not EMAIL_RE.match(clean_email):
            return {
                "exito": False,
                "mensaje": "El email del cliente no es valido para crear la orden Culqi.",
                "respuesta_completa": None,
                "error_tipo": "validation",
            }

        clean_name = _compact_text(nombre, "Cliente ACME")
        nombres = clean_name.split()
        first_name = nombres[0] if nombres else "Cliente"
        last_name = " ".join(nombres[1:]) if len(nombres) > 1 else "ACME"
        description = _compact_text(descripcion, "Pedido ACME Courier", CULQI_ORDER_DESCRIPTION_MAX_LENGTH)
        order_number = _build_order_number(pedido_id)

        payload = {
            "amount": monto,
            "currency_code": "PEN",
            "description": description,
            "order_number": order_number,
            "client_details": {
                "first_name": _compact_text(first_name, "Cliente", 30),
                "last_name": _compact_text(last_name, "ACME", 30),
                "email": clean_email,
                "phone_number": CULQI_SANDBOX_YAPE_PHONE if self._is_sandbox() else _normalize_phone(telefono),
            },
            "expiration_date": int((datetime.utcnow() + timedelta(hours=24)).timestamp()),
            "confirm": True,
            "metadata": {
                "pedido_id": str(pedido_id),
                "sistema": "ACME",
            },
        }

        try:
            result = self._request("POST", "/orders", payload)
            data = result.get("data", {})
            status = result.get("status")

            if status not in (200, 201) or not data.get("id"):
                mensaje = _culqi_error_message(data)
                logger.warning(
                    "Culqi order creation failed status=%s type=%s code=%s param=%s merchant_message=%s user_message=%s order_number=%s amount=%s",
                    status,
                    data.get("type"),
                    data.get("code"),
                    data.get("param"),
                    data.get("merchant_message"),
                    data.get("user_message"),
                    order_number,
                    monto,
                )
                return {
                    "exito": False,
                    "mensaje": mensaje,
                    "respuesta_completa": data,
                    "culqi_status": status,
                    "error_tipo": data.get("type"),
                }

            return {
                "exito": True,
                "order_id": data["id"],
                "monto_centimos": data.get("amount", monto),
                "mensaje": "Orden Culqi creada",
                "respuesta_completa": data,
            }
        except Exception as e:
            logger.error(f"Error creando orden Culqi: {str(e)}", exc_info=True)
            return {
                "exito": False,
                "mensaje": f"Error creando orden Culqi: {str(e)}",
                "respuesta_completa": None,
            }
    
    def procesar_pago(
        self,
        token: str,
        monto: int,
        email: str,
        descripcion: str,
        referencia: str,
    ) -> Dict[str, Any]:
        """
        Procesar un pago usando Culqi
        
        Args:
            token: Token generado por Culqi en el frontend
            monto: Monto en céntimos
            email: Email del cliente
            descripcion: Descripción del pago
            referencia: Referencia única (número de pedido)
        
        Returns:
            Dict con resultado de la transacción
        """
        try:
            logger.info(f"Procesando pago con Culqi - Token: {token[:20]}..., Monto: {monto}")
            
            # Crear carga (charge) en Culqi
            result = self._request("POST", "/charges", {
                "amount": monto,
                "currency_code": "PEN",
                "email": email,
                "source_id": token,
                "description": descripcion,
                "metadata": {"referencia": referencia},
            })
            charge = result.get("data", {})
            
            logger.info(f"Respuesta Culqi: {charge}")
            status = str(charge.get("status", "")).lower()
            outcome = charge.get("outcome", {})
            outcome_type = str(outcome.get("type", "")).lower()
            user_message = outcome.get("user_message", "")
            exito = (
                charge.get("object") == "charge"
                and (
                    status in ("paid", "captured", "authorized")
                    or outcome_type in ("venta_exitosa", "successful", "success")
                    or user_message == "Transacción exitosa"
                )
            )
            
            return {
                "exito": exito,
                "transaccion_id": charge.get("id"),
                "referencia": charge.get("reference_code"),
                "estado": charge.get("status"),
                "mensaje": charge.get("outcome", {}).get("user_message", "Transacción procesada"),
                "respuesta_completa": charge
            }
        
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error inesperado procesando pago: {error_msg}", exc_info=True)
            return {
                "exito": False,
                "transaccion_id": None,
                "referencia": referencia,
                "estado": "failed",
                "mensaje": f"Error procesando pago: {error_msg}",
                "respuesta_completa": None
            }
    
    def obtener_transaccion(self, transaccion_id: str) -> Dict[str, Any]:
        """Obtener estado de una transacción existente"""
        try:
            result = self._request("GET", f"/charges/{transaccion_id}")
            charge = result.get("data", {})
            return {
                "exito": True,
                "transaccion_id": charge.get("id"),
                "estado": charge.get("status"),
                "monto": charge.get("amount"),
                "respuesta_completa": charge
            }
        except Exception as e:
            logger.error(f"Error obteniendo transacción: {str(e)}")
            return {
                "exito": False,
                "mensaje": str(e)
            }

