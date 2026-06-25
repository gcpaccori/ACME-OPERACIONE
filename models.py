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

class CulqiOrderRequest(BaseModel):
    pedido_id: int
    email_cliente: EmailStr
    nombre_cliente: str
    telefono_cliente: Optional[str] = None
    descripcion: Optional[str] = None

class CulqiOrderResponse(BaseModel):
    order_id: str
    pedido_id: int
    monto_centimos: int
    mensaje: str

class CourierPaymentOrderRequest(BaseModel):
    order_id: str = Field(..., min_length=1)
    email_cliente: Optional[EmailStr] = None
    nombre_cliente: Optional[str] = None
    telefono_cliente: Optional[str] = None
    descripcion: Optional[str] = None

class CourierPaymentOrderResponse(BaseModel):
    order_id: str
    courier_order_id: str
    payment_id: str
    monto_centimos: int
    mensaje: str

class CourierPaymentChargeRequest(BaseModel):
    order_id: str = Field(..., min_length=1)
    token: str = Field(..., min_length=1)
    payment_id: Optional[str] = None
    email_cliente: Optional[EmailStr] = None
    nombre_cliente: Optional[str] = None

class CourierPaymentChargeResponse(BaseModel):
    exito: bool
    courier_order_id: str
    payment_id: Optional[str] = None
    transaccion_id: Optional[str] = None
    mensaje: str
    
class PagoStatus(BaseModel):
    transaccion_id: str
    estado: str  # "completed", "pending", "failed"
    monto: float
    fecha: datetime
    
    class Config:
        from_attributes = True

# ============ COURIER QUOTE ============
class QuoteItemInput(BaseModel):
    product_id: str = Field(..., min_length=1)
    quantity: int = Field(..., gt=0)
    modifier_ids: List[str] = Field(default_factory=list)

class CourierQuoteRequest(BaseModel):
    branch_id: str = Field(..., min_length=1)
    payment_method: str = Field(default='card')
    tip_amount: float = Field(default=0.0, ge=0)
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    fulfillment_type: str = Field(default='delivery')
    zone: Optional[str] = None
    weight_kg: float = Field(default=1.0, ge=0)
    service_type: str = Field(default='normal')
    is_difficult_zone: bool = Field(default=False)
    is_out_of_city: bool = Field(default=False)
    wait_or_second_visit: bool = Field(default=False)
    items: List[QuoteItemInput] = Field(..., min_length=1)

class CourierTariffSurcharge(BaseModel):
    code: str
    label: str
    amount: float

class CourierQuoteResponse(BaseModel):
    quote_id: str
    subtotal: float
    discount: float
    service_fee: float
    service_fee_rate: float
    delivery_fee: float
    tip_amount: float
    taxable_base: float = 0.0
    igv_rate: float = 0.18
    igv_amount: float = 0.0
    payment_processing_fee: float = 0.0
    payment_processing_rate: float = 0.0
    payment_processing_fixed: float = 0.0
    payment_processing_provider: str = "culqi"
    payment_processing_note: Optional[str] = None
    payment_processing_tax_amount: float = 0.0
    total: float
    distance_km: Optional[float] = None
    delivery_zone: Optional[str] = None
    delivery_zone_label: Optional[str] = None
    delivery_detail: Optional[str] = None
    delivery_surcharges_total: float = 0.0
    delivery_surcharges: List[CourierTariffSurcharge] = Field(default_factory=list)
    expires_at: str

class CourierReverseGeocodeResponse(BaseModel):
    line1: Optional[str] = None
    district: Optional[str] = None
    city: Optional[str] = None
    region: Optional[str] = None
    country: Optional[str] = None
    display_name: Optional[str] = None

# ============ COURIER ORDERS ============
class CourierOrderDeliveryAddress(BaseModel):
    line1: str = Field(..., min_length=1)
    line2: Optional[str] = None
    reference: Optional[str] = None
    district: Optional[str] = None
    city: Optional[str] = None
    region: Optional[str] = None
    country: str = Field(default='Peru')
    lat: Optional[float] = None
    lng: Optional[float] = None

class CourierCreateOrderRequest(BaseModel):
    quote_id: str = Field(..., min_length=1)
    fulfillment_type: str = Field(default='delivery')
    special_instructions: Optional[str] = None
    recipient_name: Optional[str] = None
    recipient_phone: Optional[str] = None
    address: Optional[CourierOrderDeliveryAddress] = None

class CourierCreateOrderResponse(BaseModel):
    order_id: str
    order_code: int
    total: float
    payment_status: str

# ============ WEBHOOK CULQI ============
class CulqiWebhookPayload(BaseModel):
    id: Optional[str] = None
    type: Optional[str] = None
    data: Optional[dict] = None

# ============ ADMIN REFUNDS ============
class AdminRefundRequest(BaseModel):
    order_id: str = Field(..., min_length=1)
    reason: Optional[str] = None
    amount: Optional[float] = None  # None = reembolso total

class AdminRefundResponse(BaseModel):
    exito: bool
    refund_id: Optional[str] = None
    amount: Optional[float] = None
    mensaje: str
