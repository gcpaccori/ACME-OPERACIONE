# ACME Ecommerce API 🛒

API FastAPI para procesamiento de compras online con integración Culqi.

## 📋 Características

- ✅ Listado de productos
- ✅ Creación de pedidos sin autenticación
- ✅ Procesamiento de pagos con Culqi
- ✅ Seguimiento de estado de pedidos
- ✅ Base de datos Supabase (PostgreSQL)
- ✅ CORS configurado para tu página web

## 🚀 Instalación

### 1. Requisitos previos
- Python 3.8+
- pip
- Supabase (PostgreSQL)
- Cuenta Culqi con credenciales

### 2. Clonar repositorio
```bash
git clone https://github.com/gcpaccori/ACME-OPERACIONE.git
cd ACME-OPERACIONE
```

### 3. Crear entorno virtual
```bash
python -m venv venv

# En Windows
venv\Scripts\activate

# En Mac/Linux
source venv/bin/activate
```

### 4. Instalar dependencias
```bash
pip install -r requirements.txt
```

### 5. Configurar variables de entorno
```bash
# Copiar archivo de ejemplo
cp .env.example .env

# Editar .env con tus credenciales
```

**Variables requeridas en `.env`:**
```
SUPABASE_URL=tu_url_supabase
SUPABASE_KEY=tu_api_key_supabase
DATABASE_URL=postgresql://user:password@host:5432/dbname

CULQI_PUBLIC_KEY=tu_clave_publica
CULQI_PRIVATE_KEY=tu_clave_privada

FRONTEND_URL=https://tu-dominio.com
```

## ▶️ Ejecutar API

```bash
# Desarrollo (con auto-reload)
uvicorn main:app --reload --host 0.0.0.0 --port 8000

# Producción
gunicorn -w 4 -k uvicorn.workers.UvicornWorker main:app
```

La API estará disponible en: **http://localhost:8000**

Documentación interactiva (Swagger): **http://localhost:8000/docs**

## 📡 Endpoints

### Productos
- `GET /api/products` - Listar todos los productos
- `GET /api/products/{producto_id}` - Obtener producto específico

### Pedidos
- `POST /api/orders` - Crear nuevo pedido
- `GET /api/orders/{pedido_id}` - Obtener estado del pedido

### Pagos
- `POST /api/pay` - Procesar pago con Culqi
- `GET /api/pay/status/{transaccion_id}` - Obtener estado del pago

### Sistema
- `GET /health` - Verificar que API funciona
- `GET /` - Información de la API

## 🔗 Integración Frontend

### 1. Listar Productos
```javascript
const response = await fetch('http://tu-api.com/api/products');
const productos = await response.json();
```

### 2. Crear Pedido
```javascript
const pedido = {
  cliente: {
    nombre: "Juan Pérez",
    email: "juan@example.com",
    telefono: "987654321",
    direccion: "Calle Principal 123",
    ciudad: "Lima",
    pais: "PE"
  },
  items: [
    { producto_id: 1, cantidad: 2 },
    { producto_id: 3, cantidad: 1 }
  ]
};

const response = await fetch('http://tu-api.com/api/orders', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(pedido)
});

const pedidoCreado = await response.json();
console.log("Pedido ID:", pedidoCreado.id);
```

### 3. Procesar Pago con Culqi
```javascript
const pago = {
  pedido_id: pedidoCreado.id,
  monto: pedidoCreado.total,
  token: tokenDeCulqi,
  email_cliente: "juan@example.com",
  nombre_cliente: "Juan Pérez",
  descripcion: "Compra en ACME"
};

const response = await fetch('http://tu-api.com/api/pay', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(pago)
});

const resultado = await response.json();
if (resultado.exito) {
  console.log("✅ Pago exitoso! Transacción:", resultado.transaccion_id);
} else {
  console.log("❌ Pago fallido:", resultado.mensaje);
}
```

### 4. Consultar Estado del Pedido
```javascript
const response = await fetch(`http://tu-api.com/api/orders/${pedidoCreado.id}`);
const pedido = await response.json();
console.log("Estado:", pedido.estado);
```

## 📚 Flujo de Compra Completo

1. **Cliente ve productos** → GET `/api/products`
2. **Cliente crea pedido** → POST `/api/orders`
3. **Frontend genera token Culqi** → [Culqi Widget]
4. **Backend procesa pago** → POST `/api/pay`
5. **Pedido se marca como PAGADO** → Transacción guardada
6. **Cliente consulta estado** → GET `/api/orders/{id}`

## 🔐 Seguridad

- Claves de Culqi en variables de entorno (.env)
- CORS restringido a tu dominio
- Validación de datos con Pydantic
- Manejo seguro de tokens

## 🛠️ Desarrollo

### Crear datos de prueba

```python
from database import SessionLocal
from schemas import Producto

db = SessionLocal()

# Crear producto
producto = Producto(
    nombre="Laptop",
    descripcion="Laptop de 15 pulgadas",
    precio=1500.00,
    stock=10,
    sku="LAP-001"
)
db.add(producto)
db.commit()
```

### Probar API con curl

```bash
# Listar productos
curl http://localhost:8000/api/products

# Crear pedido
curl -X POST http://localhost:8000/api/orders \
  -H "Content-Type: application/json" \
  -d '{...}'
```

## 📝 Documentación

- **INICIO_AQUI.md** - Guía de inicio rápido
- **INSTALL.md** - Instalación paso a paso
- **ESTRUCTURA.md** - Estructura del proyecto
- **TIPS.md** - Tips y troubleshooting
- **CHECKLIST.md** - Checklist de implementación

## ❓ Troubleshooting

### Error: `No module named 'culqi'`
```bash
pip install culqi==2.2.6
```

### Error: `Connection refused` en Supabase
- Verificar `DATABASE_URL` en `.env`
- Verificar que Supabase está corriendo
- Revisar credenciales

### Error: `Invalid token` de Culqi
- Verificar que `CULQI_PRIVATE_KEY` es correcta
- Asegurar que el token viene del frontend correcto

## 📞 Soporte

Para preguntas o problemas, revisa:
1. Logs de la API (`uvicorn`)
2. Consola del navegador (frontend)
3. Panel de Supabase
4. Dashboard de Culqi

## 📄 Licencia

MIT

---

**¡Tu ecommerce está listo! 🎉**