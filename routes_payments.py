from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from schemas import Transaccion, EstadoTransaccion
from models import PagoRequest, PagoResponse
from services_culqi import CulqiService
from services_order import OrderService
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pay", tags=["payments"])

culqi_service = CulqiService()

@router.post("/", response_model=PagoResponse)
def procesar_pago(pago: PagoRequest, db: Session = Depends(get_db)):
    """
    Procesar un pago con Culqi
    
    - **pedido_id**: ID del pedido creado
    - **monto**: Monto a cobrar (en soles)
    - **token**: Token generado por Culqi en el frontend
    - **email_cliente**: Email del cliente
    - **nombre_cliente**: Nombre del cliente
    """
    
    try:
        # 1. Validar que el pedido existe
        pedido = OrderService.obtener_pedido(db, pago.pedido_id)
        if not pedido:
            raise HTTPException(status_code=404, detail="Pedido no encontrado")
        
        # 2. Convertir monto a céntimos (Culqi trabaja en céntimos)
        monto_centimos = int(pago.monto * 100)
        
        # 3. Procesar pago con Culqi
        resultado_culqi = culqi_service.procesar_pago(
            token=pago.token,
            monto=monto_centimos,
            email=pago.email_cliente,
            descripcion=pago.descripcion or f"Pago Pedido #{pago.pedido_id}",
            referencia=f"pedido-{pago.pedido_id}"
        )
        
        # 4. Registrar transacción en BD
        exito = resultado_culqi["exito"]
        estado_transaccion = EstadoTransaccion.COMPLETADA if exito else EstadoTransaccion.FALLIDA
        
        OrderService.registrar_transaccion(
            db=db,
            pedido_id=pago.pedido_id,
            transaccion_id=resultado_culqi.get("transaccion_id", ""),
            monto=pago.monto,
            estado=estado_transaccion,
            referencia=resultado_culqi.get("referencia", ""),
            mensaje=resultado_culqi.get("mensaje", "")
        )
        
        return PagoResponse(
            exito=exito,
            transaccion_id=resultado_culqi.get("transaccion_id"),
            pedido_id=pago.pedido_id,
            mensaje=resultado_culqi.get("mensaje", "Pago procesado correctamente" if exito else "Error al procesar pago")
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error procesando pago: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error procesando pago: {str(e)}")

@router.get("/status/{transaccion_id}")
def obtener_estado_pago(transaccion_id: str, db: Session = Depends(get_db)):
    """
    Obtener el estado de una transacción
    """
    transaccion = db.query(Transaccion).filter(
        Transaccion.transaccion_id == transaccion_id
    ).first()
    
    if not transaccion:
        raise HTTPException(status_code=404, detail="Transacción no encontrada")
    
    return {
        "transaccion_id": transaccion.transaccion_id,
        "pedido_id": transaccion.pedido_id,
        "estado": transaccion.estado.value,
        "monto": transaccion.monto,
        "fecha": transaccion.created_at
    }
