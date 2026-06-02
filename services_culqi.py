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
            charge = culqi.Charge.create(
                amount=monto,
                currency_code="PEN",
                email=email,
                source_id=token,
                description=descripcion,
                metadata={"referencia": referencia}
            )
            
            logger.info(f"Respuesta Culqi: {charge}")
            
            exito = charge.get("object") == "charge" and charge.get("outcome", {}).get("user_message") == "Transacción exitosa"
            
            return {
                "exito": exito,
                "transaccion_id": charge.get("id"),
                "referencia": charge.get("reference_code"),
                "estado": charge.get("status"),
                "mensaje": charge.get("outcome", {}).get("user_message", "Transacción procesada"),
                "respuesta_completa": charge
            }
        
        except culqi.error.CulqiError as e:
            error_msg = str(e)
            logger.error(f"Error Culqi: {error_msg}")
            return {
                "exito": False,
                "transaccion_id": None,
                "referencia": referencia,
                "estado": "failed",
                "mensaje": f"Error Culqi: {error_msg}",
                "respuesta_completa": None
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

