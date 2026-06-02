"""
Script para agregar datos de prueba a la base de datos
Ejecutar con: python seed_data.py
"""

from database import SessionLocal
from schemas import Producto
from datetime import datetime

def seed_products():
    db = SessionLocal()
    
    # Verificar si ya hay productos
    existing = db.query(Producto).count()
    if existing > 0:
        print(f"⚠️  Ya hay {existing} productos en la BD")
        db.close()
        return
    
    # Crear productos de prueba
    productos = [
        Producto(
            nombre="Laptop Dell XPS 13",
            descripcion="Laptop ultraportátil con procesador Intel i7",
            precio=1200.00,
            stock=5,
            sku="LAP-001"
        ),
        Producto(
            nombre="Mouse Logitech MX Master",
            descripcion="Mouse inalámbrico profesional",
            precio=89.99,
            stock=15,
            sku="MOU-001"
        ),
        Producto(
            nombre="Teclado Mecánico Corsair",
            descripcion="Teclado mecánico RGB para gaming",
            precio=149.99,
            stock=8,
            sku="KEY-001"
        ),
        Producto(
            nombre="Monitor LG 27 4K",
            descripcion="Monitor 4K de 27 pulgadas",
            precio=399.99,
            stock=3,
            sku="MON-001"
        )
    ]
    
    for producto in productos:
        db.add(producto)
    
    db.commit()
    print("✅ 4 productos agregados exitosamente!")
    
    # Mostrar productos
    todos = db.query(Producto).all()
    for p in todos:
        print(f"  - {p.nombre} (${p.precio}) - Stock: {p.stock}")
    
    db.close()

if __name__ == "__main__":
    seed_products()

