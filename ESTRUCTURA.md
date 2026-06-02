# 📦 ESTRUCTURA DEL PROYECTO FASTAPI

```
ACME-OPERACIONE/
│
├── 📄 main.py                      ← ARCHIVO PRINCIPAL (Ejecutar esto)
├── 📄 config.py                    ← Configuración de la app
├── 📄 database.py                  ← Conexión a Supabase
├── 📄 schemas.py                   ← Modelos SQLAlchemy (BD)
├── 📄 models.py                    ← Modelos Pydantic (API)
│
├── 🔧 services_culqi.py            ← Lógica de pagos Culqi
├── 🔧 services_order.py            ← Lógica de pedidos
│
├── 🛣️ routes_products.py           ← Endpoints de productos
├── 🛣️ routes_orders.py             ← Endpoints de pedidos
├── 🛣️ routes_payments.py           ← Endpoints de pagos
│
├── 📋 requirements.txt              ← Dependencias Python
├── .env.example                     ← Plantilla de variables de entorno
├── .env                             ← Variables de entorno (NO COMMITTEAR)
│
├── 📚 README.md                     ← Documentación completa
├── 📚 INSTALL.md                    ← Guía de instalación
├── 📚 ESTRUCTURA.md                 ← Este archivo
│
├── 🌐 ejemplo_frontend.html         ← Ejemplo de página web
│
├── 🗄️ setup_database.sql            ← Script para crear tablas en Supabase
├── 🧪 test_api.py                   ← Script de pruebas
│
└── .git/                            ← Repositorio Git
```

## 🔄 FLUJO DE DATOS

```
┌─────────────────┐
│  PÁGINA WEB     │
│  (HTML + JS)    │
└────────┬────────┘
         │ 1. Listar productos
         ▼
    ┌─────────────┐
    │  API FastAPI  │
    │  main.py    │
    └────┬────┬────┘
         │    │
         │    └─────── routes_products.py
         │             GET /api/products
         │             GET /api/products/{id}
         │
         ├─────── routes_orders.py
         │        POST /api/orders
         │        GET /api/orders/{id}
         │
         └─────── routes_payments.py
                  POST /api/pay
                  GET /api/pay/status/{id}
         │
    ┌────┴──────────────┐
    │                   │
    ▼                   ▼
┌──────────┐      ┌──────────┐
│ SUPABASE │      │  CULQI   │
│(PostgreSQL)      │ (Pagos)  │
│ BD       │      │ API      │
└──────────┘      └──────────┘
   (usuarios,       (procesa
    productos,       pagos)
    pedidos,
    transacciones)
```

## 📡 ENDPOINTS DISPONIBLES

### 1. PRODUCTOS
```
GET  /api/products              ← Listar todos
GET  /api/products/{id}         ← Obtener uno
```

### 2. PEDIDOS
```
POST /api/orders                ← Crear nuevo
GET  /api/orders/{id}           ← Obtener estado
```

### 3. PAGOS
```
POST /api/pay                   ← Procesar pago
GET  /api/pay/status/{id}       ← Obtener estado de pago
```

### 4. SISTEMA
```
GET  /health                    ← Health check
GET  /                          ← Info de API
GET  /docs                      ← Swagger UI
GET  /redoc                     ← ReDoc
```

## 🗄️ ESTRUCTURA DE BASES DE DATOS

```
┌─────────────────────┐
│   PRODUCTOS         │
├─────────────────────┤
│ id (PK)            │
│ nombre             │
│ descripcion        │
│ precio             │
│ stock              │
│ sku (UNIQUE)       │
│ created_at         │
│ updated_at         │
└─────────────────────┘

┌─────────────────────┐      ┌──────────────────────┐
│    CLIENTES         │      │     PEDIDOS          │
├─────────────────────┤      ├──────────────────────┤
│ id (PK)            │◀─────│ cliente_id (FK)      │
│ nombre             │      │ id (PK)              │
│ email (UNIQUE)     │      │ estado               │
│ telefono           │      │ total                │
│ direccion          │      │ created_at           │
│ ciudad             │      │ updated_at           │
│ pais               │      └──────────────────────┘
│ created_at         │
│ updated_at         │      ┌──────────────────────┐
└─────────────────────┘      │   ITEMS_PEDIDO       │
                             ├──────────────────────┤
                             │ pedido_id (FK)      │
                             │ producto_id (FK)    │
                             │ cantidad            │
                             │ precio_unitario     │
                             └──────────────────────┘

┌──────────────────────────┐
│   TRANSACCIONES          │
├──────────────────────────┤
│ id (PK)                 │
│ pedido_id (FK, UNIQUE) │
│ transaccion_id (UNIQUE) │
│ monto                   │
│ estado                  │
│ referencia              │
│ mensaje                 │
│ created_at              │
│ updated_at              │
└──────────────────────────┘
```

## 🔐 VARIABLES DE ENTORNO

```
# Supabase
SUPABASE_URL              → URL de tu proyecto
SUPABASE_KEY              → Clave pública API
DATABASE_URL              → Connection string PostgreSQL

# Culqi
CULQI_PUBLIC_KEY          → Para generar tokens en frontend
CULQI_PRIVATE_KEY         → Para procesar pagos en backend

# Configuración
API_HOST                  → Host (0.0.0.0 para todos)
API_PORT                  → Puerto (8000)
DEBUG                     → True/False
FRONTEND_URL              → URL de tu página web (para CORS)
```

## 📂 ARCHIVOS IMPORTANTES

| Archivo | Propósito |
|---------|-----------|
| `main.py` | Punto de entrada, crea la app FastAPI |
| `config.py` | Lee variables de entorno |
| `database.py` | Conexión y sesión de Supabase |
| `schemas.py` | Tablas de BD (SQLAlchemy ORM) |
| `models.py` | Esquemas de datos (Pydantic) |
| `services_*.py` | Lógica de negocio |
| `routes_*.py` | Endpoints HTTP |
| `.env` | Credenciales (⚠️ NO committear) |
| `requirements.txt` | Dependencias pip |
| `setup_database.sql` | Script SQL para Supabase |
| `ejemplo_frontend.html` | Referencia de integración |

## 🎯 ESTADOS DE PEDIDO

```
PENDIENTE  → Creado, esperando pago
PAGADO     → Pago completado
ENVIADO    → En tránsito
ENTREGADO  → Entregado al cliente
CANCELADO  → Cancelado
```

## 🎯 ESTADOS DE TRANSACCIÓN

```
PENDING    → Esperando procesamiento
COMPLETED  → Pago exitoso
FAILED     → Pago rechazado
```

## 🚀 PARA EMPEZAR

1. **Instalar** → `pip install -r requirements.txt`
2. **Configurar** → Editar `.env` con credenciales
3. **BD** → Ejecutar `setup_database.sql` en Supabase
4. **Ejecutar** → `uvicorn main:app --reload`
5. **Documentación** → http://localhost:8000/docs
6. **Probar** → `python test_api.py`
7. **Integrar** → Ver `ejemplo_frontend.html`

---

**¡Listo para empezar! 🎉**
