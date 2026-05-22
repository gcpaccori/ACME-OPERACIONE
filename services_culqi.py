import culqi
from config import settings
from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)

class CulqiService:
    """Servicio para procesar pagos con Culqi"""
    
    def __init__(self):
        # Configurar Culqi con la clave privada
        culqi.public_key = settings.culqi_public_key
        culqi.private_key = settings.culqi_private_key
    
    def procesar_pago(
        self,
        token: str,
        monto: int,
        email: str,
        descripcion: str,
        referencia: str,
    ) -> Dict[str, Any]:
        """Procesar un pago usando Culqi"""
        try:
            charge = culqi.Charge.create(
                amount=monto,
                currency_code="PEN",
                email=email,
                source_id=token,
                description=descripcion,
                metadata={"referencia": referencia}
            )
            
            return {
                "exito": charge.get("object") == "charge" and charge.get("outcome", {}).get("user_message") == "Transacción exitosa",
                "transaccion_id": charge.get("id"),
                "referencia": charge.get("reference_code"),
                "estado": charge.get("status"),
                "mensaje": charge.get("outcome", {}).get("user_message", ""),
                "respuesta_completa": charge
            }
        
        except culqi.error.CulqiError as e:
            logger.error(f"Error Culqi: {str(e)}")
            return {
                "exito": False,
                "transaccion_id": None,
                "referencia": referencia,
                "estado": "failed",
                "mensaje": str(e),
                "respuesta_completa": None
            }
        except Exception as e:
            logger.error(f"Error inesperado procesando pago: {str(e)}")
            return {
                "exito": False,
                "transaccion_id": None,
                "referencia": referencia,
                "estado": "failed",
                "mensaje": "Error procesando pago",
                "respuesta_completa": None
            }
    
    def obtener_transaccion(self, transaccion_id: str) -> Dict[str, Any]:
        """Obtener estado de una transacción existente"""
        try:
            charge = culqi.Charge.retrieve(transaccion_id)
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