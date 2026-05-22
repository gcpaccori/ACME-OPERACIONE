from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from schemas import Pedido as PedidoModel
from models import PedidoCreate, Pedido as PedidoSchema
from services_order import OrderService
from typing import List

router = APIRouter(prefix="/api/orders", tags=["orders"])

@router.post("/", response_model=PedidoSchema)
def crear_pedido(pedido_data: PedidoCreate, db: Session = Depends(get_db)):
    """Crear un nuevo pedido"""
    pedido, error = OrderService.crear_pedido(db, pedido_data)
    
    if error:
        raise HTTPException(status_code=400, detail=error)
    
    if not pedido:
        raise HTTPException(status_code=500, detail="Error al crear pedido")
    
    return pedido

@router.get("/{pedido_id}", response_model=PedidoSchema)
def obtener_pedido(pedido_id: int, db: Session = Depends(get_db)):
    """Obtener detalles de un pedido y su estado"""
    pedido = OrderService.obtener_pedido(db, pedido_id)
    
    if not pedido:
        raise HTTPException(status_code=404, detail="Pedido no encontrado")
    
    return pedido