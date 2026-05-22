from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from schemas import Producto as ProductoModel
from models import Producto as ProductoSchema
from typing import List

router = APIRouter(prefix="/api/products", tags=["products"])

@router.get("/", response_model=List[ProductoSchema])
def listar_productos(db: Session = Depends(get_db)):
    """Listar todos los productos disponibles"""
    productos = db.query(ProductoModel).filter(ProductoModel.stock > 0).all()
    return productos

@router.get("/{producto_id}", response_model=ProductoSchema)
def obtener_producto(producto_id: int, db: Session = Depends(get_db)):
    """Obtener detalles de un producto específico"""
    producto = db.query(ProductoModel).filter(
        ProductoModel.id == producto_id
    ).first()
    
    if not producto:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    
    return producto