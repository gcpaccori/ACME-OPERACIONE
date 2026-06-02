"""
Script de prueba para validar la API
Ejecutar con: python test_api.py
"""

import requests
import json
from datetime import datetime

API_URL = "http://localhost:8000"

class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    END = '\033[0m'

def print_test(nombre, resultado, detalles=""):
    """Imprimir resultado de prueba"""
    color = Colors.GREEN if resultado else Colors.RED
    estado = "✅ PASS" if resultado else "❌ FAIL"
    print(f"{color}{estado}{Colors.END} | {nombre}")
    if detalles:
        print(f"   → {detalles}")

def test_health():
    """Probar que la API está funcionando"""
    try:
        resp = requests.get(f"{API_URL}/health")
        resultado = resp.status_code == 200
        print_test("Health Check", resultado, f"Status: {resp.status_code}")
        return resultado
    except Exception as e:
        print_test("Health Check", False, str(e))
        return False

def test_listar_productos():
    """Probar listado de productos"""
    try:
        resp = requests.get(f"{API_URL}/api/products")
        resultado = resp.status_code == 200
        data = resp.json() if resultado else []
        print_test("GET /api/products", resultado, f"Productos encontrados: {len(data)}")
        return resultado, data
    except Exception as e:
        print_test("GET /api/products", False, str(e))
        return False, []

def test_crear_pedido(productos):
    """Probar crear un pedido"""
    if not productos:
        print_test("POST /api/orders", False, "No hay productos disponibles")
        return False, None
    
    try:
        pedido_data = {
            "cliente": {
                "nombre": f"Test Cliente {datetime.now().timestamp()}",
                "email": f"test{datetime.now().timestamp()}@example.com",
                "telefono": "987654321",
                "direccion": "Calle de Prueba 123",
                "ciudad": "Lima",
                "pais": "PE"
            },
            "items": [
                {
                    "producto_id": productos[0]["id"],
                    "cantidad": 1
                }
            ]
        }
        
        resp = requests.post(f"{API_URL}/api/orders", json=pedido_data)
        resultado = resp.status_code == 200
        pedido = resp.json() if resultado else None
        
        detalles = f"Pedido ID: {pedido.get('id')}, Total: {pedido.get('total')}" if pedido else "Error"
        print_test("POST /api/orders", resultado, detalles)
        
        return resultado, pedido
    except Exception as e:
        print_test("POST /api/orders", False, str(e))
        return False, None

def test_obtener_pedido(pedido_id):
    """Probar obtener detalles del pedido"""
    if not pedido_id:
        print_test("GET /api/orders/{id}", False, "ID de pedido no disponible")
        return False
    
    try:
        resp = requests.get(f"{API_URL}/api/orders/{pedido_id}")
        resultado = resp.status_code == 200
        data = resp.json() if resultado else {}
        
        detalles = f"Estado: {data.get('estado')}, Total: {data.get('total')}" if resultado else "No encontrado"
        print_test("GET /api/orders/{id}", resultado, detalles)
        
        return resultado
    except Exception as e:
        print_test("GET /api/orders/{id}", False, str(e))
        return False

def test_producto_especifico(producto_id):
    """Probar obtener producto específico"""
    try:
        resp = requests.get(f"{API_URL}/api/products/{producto_id}")
        resultado = resp.status_code == 200
        data = resp.json() if resultado else {}
        
        detalles = f"Nombre: {data.get('nombre')}, Precio: {data.get('precio')}" if resultado else "No encontrado"
        print_test(f"GET /api/products/{producto_id}", resultado, detalles)
        
        return resultado
    except Exception as e:
        print_test(f"GET /api/products/{producto_id}", False, str(e))
        return False

def run_all_tests():
    """Ejecutar todas las pruebas"""
    print(f"\n{Colors.BLUE}{'='*60}")
    print("PRUEBAS DE API - ACME ECOMMERCE")
    print(f"{'='*60}{Colors.END}\n")
    
    # Test 1: Health check
    if not test_health():
        print(f"\n{Colors.RED}❌ API no está disponible en {API_URL}{Colors.END}")
        print("Asegúrate de que la API está corriendo: uvicorn main:app --reload\n")
        return
    
    print()
    
    # Test 2: Listar productos
    test_ok, productos = test_listar_productos()
    
    print()
    
    # Test 3: Crear pedido
    if productos:
        test_ok, pedido = test_crear_pedido(productos)
        
        print()
        
        # Test 4: Obtener pedido
        if pedido:
            test_obtener_pedido(pedido.get("id"))
        
        print()
        
        # Test 5: Obtener producto específico
        test_producto_especifico(productos[0]["id"])
    
    print(f"\n{Colors.BLUE}{'='*60}")
    print("✅ PRUEBAS COMPLETADAS")
    print(f"{'='*60}{Colors.END}\n")
    
    print(f"{Colors.YELLOW}📝 Notas:{Colors.END}")
    print("- Las pruebas de pago (Culqi) requieren un token válido")
    print("- Para probar pagos, usa el ejemplo_frontend.html")
    print("- Documentación completa en: http://localhost:8000/docs\n")

if __name__ == "__main__":
    try:
        run_all_tests()
    except Exception as e:
        print(f"{Colors.RED}Error: {str(e)}{Colors.END}\n")
