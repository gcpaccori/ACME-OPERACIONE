from config import settings
from typing import Dict, Any
from datetime import datetime, timedelta
import requests
import logging

logger = logging.getLogger(__name__)

CULQI_API_BASE = "https://api.culqi.com/v2"

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
        nombres = nombre.strip().split()
        first_name = nombres[0] if nombres else "Cliente"
        last_name = " ".join(nombres[1:]) if len(nombres) > 1 else "ACME"
        safe_reference = "".join(ch for ch in str(pedido_id) if ch.isalnum() or ch in ("-", "_"))[:32]

        payload = {
            "amount": monto,
            "currency_code": "PEN",
            "description": descripcion,
            "order_number": f"pedido-{safe_reference}-{int(datetime.utcnow().timestamp())}",
            "client_details": {
                "first_name": first_name,
                "last_name": last_name,
                "email": email,
                "phone_number": telefono or "51999999999",
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
                mensaje = data.get("user_message") or data.get("merchant_message") or "No se pudo crear la orden Culqi"
                return {
                    "exito": False,
                    "mensaje": mensaje,
                    "respuesta_completa": data,
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

