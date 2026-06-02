from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text, Enum
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime
import enum

Base = declarative_base()

class EstadoPedido(str, enum.Enum):
    PENDIENTE = "pendiente"
    PAGADO = "pagado"
    ENVIADO = "enviado"
    ENTREGADO = "entregado"
    CANCELADO = "cancelado"

class EstadoTransaccion(str, enum.Enum):
    PENDIENTE = "pending"
    COMPLETADA = "completed"
    FALLIDA = "failed"

# ============ TABLA: PRODUCTOS ============
class Producto(Base):
    __tablename__ = "productos"
    
    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String(255), nullable=False)
    descripcion = Column(Text, nullable=True)
    precio = Column(Float, nullable=False)
    stock = Column(Integer, default=0)
    sku = Column(String(100), unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relaciones
    items_pedido = relationship("ItemPedido", back_populates="producto")

# ============ TABLA: CLIENTES ============
class Cliente(Base):
    __tablename__ = "clientes"
    
    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String(255), nullable=False)
    email = Column(String(255), unique=True, nullable=False, index=True)
    telefono = Column(String(20), nullable=True)
    direccion = Column(String(500), nullable=False)
    ciudad = Column(String(100), nullable=False)
    pais = Column(String(2), default="PE")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relaciones
    pedidos = relationship("Pedido", back_populates="cliente")

# ============ TABLA: PEDIDOS ============
class Pedido(Base):
    __tablename__ = "pedidos"
    
    id = Column(Integer, primary_key=True, index=True)
    cliente_id = Column(Integer, ForeignKey("clientes.id"), nullable=False)
    estado = Column(Enum(EstadoPedido), default=EstadoPedido.PENDIENTE)
    total = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relaciones
    cliente = relationship("Cliente", back_populates="pedidos")
    items = relationship("ItemPedido", back_populates="pedido", cascade="all, delete-orphan")
    transaccion = relationship("Transaccion", back_populates="pedido", uselist=False)

# ============ TABLA: ITEMS DEL PEDIDO ============
class ItemPedido(Base):
    __tablename__ = "items_pedido"
    
    id = Column(Integer, primary_key=True, index=True)
    pedido_id = Column(Integer, ForeignKey("pedidos.id"), nullable=False)
    producto_id = Column(Integer, ForeignKey("productos.id"), nullable=False)
    cantidad = Column(Integer, nullable=False)
    precio_unitario = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relaciones
    pedido = relationship("Pedido", back_populates="items")
    producto = relationship("Producto", back_populates="items_pedido")

# ============ TABLA: TRANSACCIONES (CULQI) ============
class Transaccion(Base):
    __tablename__ = "transacciones"
    
    id = Column(Integer, primary_key=True, index=True)
    pedido_id = Column(Integer, ForeignKey("pedidos.id"), unique=True, nullable=False)
    transaccion_id = Column(String(100), unique=True, nullable=False, index=True)  # ID de Culqi
    monto = Column(Float, nullable=False)
    estado = Column(Enum(EstadoTransaccion), default=EstadoTransaccion.PENDIENTE)
    referencia = Column(String(255), nullable=True)  # Referencia de Culqi
    mensaje = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relaciones
    pedido = relationship("Pedido", back_populates="transaccion")
