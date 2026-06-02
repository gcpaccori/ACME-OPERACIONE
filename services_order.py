from sqlalchemy.orm import Session
from schemas import Pedido, Cliente, ItemPedido, Producto, Transaccion, EstadoPedido, EstadoTransaccion
from models import PedidoCreate
import logging

logger = logging.getLogger(__name__)

class OrderService:
    """Servicio para gestionar pedidos"""
    
    @staticmethod
    def crear_pedido(db: Session, pedido_data: PedidoCreate) -> tuple:
        """
        Crear un nuevo pedido con cliente e items
        
        Returns:
            (pedido_creado, error_message)
            Si error_message no es vacío, algo salió mal
        """
        try:
            # 1. Crear o buscar cliente
            cliente = db.query(Cliente).filter(
                Cliente.email == pedido_data.cliente.email
            ).first()
            
            if not cliente:
                cliente = Cliente(
                    nombre=pedido_data.cliente.nombre,
                    email=pedido_data.cliente.email,
                    telefono=pedido_data.cliente.telefono,
                    direccion=pedido_data.cliente.direccion,
                    ciudad=pedido_data.cliente.ciudad,
                    pais=pedido_data.cliente.pais
                )
                db.add(cliente)
                db.flush()
            
            # 2. Crear pedido
            pedido = Pedido(
                cliente_id=cliente.id,
                estado=EstadoPedido.PENDIENTE,
                total=0.0
            )
            db.add(pedido)
            db.flush()
            
            # 3. Agregar items del pedido
            total = 0.0
            for item_data in pedido_data.items:
                producto = db.query(Producto).filter(
                    Producto.id == item_data.producto_id
                ).first()
                
                if not producto:
                    db.rollback()
                    return None, f"Producto {item_data.producto_id} no encontrado"
                
                if producto.stock < item_data.cantidad:
                    db.rollback()
                    return None, f"Stock insuficiente para {producto.nombre}"
                
                precio_unitario = producto.precio
                subtotal = precio_unitario * item_data.cantidad
                total += subtotal
                
                item = ItemPedido(
                    pedido_id=pedido.id,
                    producto_id=producto.id,
                    cantidad=item_data.cantidad,
                    precio_unitario=precio_unitario
                )
                db.add(item)
                
                # Descontar del stock
                producto.stock -= item_data.cantidad
            
            pedido.total = total
            db.commit()
            db.refresh(pedido)
            
            return pedido, ""
        
        except Exception as e:
            db.rollback()
            logger.error(f"Error creando pedido: {str(e)}")
            return None, str(e)
    
    @staticmethod
    def obtener_pedido(db: Session, pedido_id: int) -> Pedido:
        """Obtener pedido por ID"""
        return db.query(Pedido).filter(Pedido.id == pedido_id).first()
    
    @staticmethod
    def actualizar_estado_pedido(
        db: Session,
        pedido_id: int,
        nuevo_estado: EstadoPedido
    ) -> bool:
        """Actualizar el estado del pedido"""
        try:
            pedido = db.query(Pedido).filter(Pedido.id == pedido_id).first()
            if pedido:
                pedido.estado = nuevo_estado
                db.commit()
                return True
            return False
        except Exception as e:
            db.rollback()
            logger.error(f"Error actualizando estado: {str(e)}")
            return False
    
    @staticmethod
    def registrar_transaccion(
        db: Session,
        pedido_id: int,
        transaccion_id: str,
        monto: float,
        estado: EstadoTransaccion,
        referencia: str = None,
        mensaje: str = None
    ) -> bool:
        """Registrar transacción de pago"""
        try:
            transaccion = Transaccion(
                pedido_id=pedido_id,
                transaccion_id=transaccion_id,
                monto=monto,
                estado=estado,
                referencia=referencia,
                mensaje=mensaje
            )
            db.add(transaccion)
            
            # Si el pago fue exitoso, actualizar estado del pedido
            if estado == EstadoTransaccion.COMPLETADA:
                pedido = db.query(Pedido).filter(Pedido.id == pedido_id).first()
                if pedido:
                    pedido.estado = EstadoPedido.PAGADO
            
            db.commit()
            return True
        except Exception as e:
            db.rollback()
            logger.error(f"Error registrando transacción: {str(e)}")
            return False

