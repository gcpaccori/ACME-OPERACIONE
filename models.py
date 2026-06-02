from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List
from datetime import datetime

# ============ PRODUCTOS ============
class ProductoBase(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=255)
    descripcion: Optional[str] = None
    precio: float = Field(..., gt=0)
    stock: int = Field(default=0, ge=0)
    sku: str = Field(..., min_length=1, max_length=100)

class ProductoCreate(ProductoBase):
    pass

class Producto(ProductoBase):
    id: int
    created_at: datetime
    
    class Config:
        from_attributes = True

# ============ CLIENTES ============
class ClienteBase(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=255)
    email: EmailStr
    telefono: Optional[str] = None
    direccion: str = Field(..., min_length=5)
    ciudad: str = Field(..., min_length=1)
    pais: str = Field(default="PE", max_length=2)

class ClienteCreate(ClienteBase):
    pass

class Cliente(ClienteBase):
    id: int
    created_at: datetime
    
    class Config:
        from_attributes = True

# ============ ITEMS DEL PEDIDO ============
class ItemPedidoBase(BaseModel):
    producto_id: int
    cantidad: int = Field(..., gt=0)
    precio_unitario: float = Field(..., gt=0)

class ItemPedido(ItemPedidoBase):
    id: int
    pedido_id: int
    
    class Config:
        from_attributes = True

# ============ PEDIDOS ============
class PedidoItemCreate(BaseModel):
    producto_id: int
    cantidad: int = Field(..., gt=0)

class PedidoCreate(BaseModel):
    cliente: ClienteCreate
    items: List[PedidoItemCreate] = Field(..., min_items=1)
    
class Pedido(BaseModel):
    id: int
    cliente_id: int
    estado: str
    total: float
    items: List[ItemPedido] = []
    created_at: datetime
    
    class Config:
        from_attributes = True

# ============ PAGOS (CULQI) ============
class PagoRequest(BaseModel):
    pedido_id: int
    monto: float = Field(..., gt=0)
    descripcion: Optional[str] = None
    email_cliente: EmailStr
    nombre_cliente: str
    
    # Token de Culqi (generado en el frontend)
    token: str

class PagoResponse(BaseModel):
    exito: bool
    transaccion_id: Optional[str] = None
    pedido_id: int
    mensaje: str
    
class PagoStatus(BaseModel):
    transaccion_id: str
    estado: str  # "completed", "pending", "failed"
    monto: float
    fecha: datetime
    
    class Config:
        from_attributes = True
