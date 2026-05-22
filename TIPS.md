# 💡 TIPS Y TROUBLESHOOTING

## 🚀 CONSEJOS DE USO

### 1. Desarrollo Local
- Mantén `DEBUG=True` en `.env` durante desarrollo
- Usa `--reload` en uvicorn para ver cambios automáticos
- Abre http://localhost:8000/docs para documentación interactiva

### 2. CORS y Frontend
Si tu frontend está en un dominio diferente, agrega en FRONTEND_URL:
```python
allow_origins=[
    "http://localhost:3000",
    "http://localhost:5173",
    "https://mi-dominio.com"
]
```

### 3. Culqi en Desarrollo
- Usa credenciales de **TEST** (pk_test_*, sk_test_*)
- No uses credenciales de producción en desarrollo
- Los pagos de prueba no se cobran

### 4. Variables de Entorno
```bash
# Crear .env desde .env.example
cp .env.example .env

# IMPORTANTE: NUNCA commitear .env
# Agregar a .gitignore:
echo ".env" >> .gitignore
```

---

## 🔧 TROUBLESHOOTING

### ❌ Error: "No module named 'fastapi'"

**Solución:**
```bash
# Asegúrate de que venv está activado
# En Windows:
venv\Scripts\activate

# Luego instala dependencias
pip install -r requirements.txt
```

### ❌ Error: "connection refused" (Supabase)

**Causas posibles:**
1. DATABASE_URL incorrecta
2. Supabase no está disponible
3. Firewall bloqueando conexión

**Solución:**
```bash
# Verificar DATABASE_URL en .env
# Copiarlo desde Supabase > Project Settings > Database > Connection String

# Probar conexión manualmente:
python -c "
import os
from database import engine
from sqlalchemy import text
try:
    with engine.connect() as conn:
        result = conn.execute(text('SELECT 1'))
        print('✅ Conexión OK')
except Exception as e:
    print(f'❌ Error: {e}')
"
```

### ❌ Error: "404 Not Found" en endpoints

**Solución:**
- Verifica que los archivos de rutas están siendo importados en main.py
- Los includes deben ser `app.include_router(routes_products.router)` etc.
- Reinicia la API

### ❌ Error: "Invalid token" de Culqi

**Causas:**
1. Token expirado (durabilidad ~10 minutos)
2. Token de dominio incorrecto
3. Clave pública no coincide

**Solución:**
- Generar nuevo token en frontend
- Verificar que clave pública en HTML coincide con CULQI_PUBLIC_KEY
- Revisar logs de Culqi

### ❌ Error: "CORS policy: No 'Access-Control-Allow-Origin'"

**Solución:**
```python
# En main.py, asegúrate que CORS está bien configurado:
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "https://mi-dominio.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

### ❌ Error: "Duplicate entry" al crear cliente

**Nota:** Esto es normal en desarrollo. Los emails deben ser únicos.

**Solución:** Usar emails diferentes en cada prueba:
```javascript
// En frontend
email: `test-${Date.now()}@example.com`
```

### ❌ Error: "Stock insuficiente"

**Significa:** No hay suficiente inventario

**Solución:**
1. Agregar más stock en Supabase
2. O reducir cantidad en el pedido

### ❌ API lenta o timeout

**Causas:**
1. Conexión a BD lenta
2. Culqi tardando en responder
3. Demasiadas conexiones

**Solución:**
```bash
# Aumentar timeout en requests (frontend)
const response = await fetch(url, {
  signal: AbortSignal.timeout(30000) // 30 segundos
});

# O aumentar en fastapi
import httpx
timeout = httpx.Timeout(30.0, connect=10.0)
```

---

## 📄 DEBUGGING

### Ver logs completos
```bash
# Ejecutar con nivel DEBUG
uvicorn main:app --reload --log-level debug
```

### Consultar BD directamente
```bash
# En Supabase SQL Editor
SELECT * FROM productos;
SELECT * FROM pedidos ORDER BY created_at DESC;
SELECT * FROM transacciones;
```

### Probar endpoints con curl
```bash
# Listar productos
curl http://localhost:8000/api/products

# Crear pedido
curl -X POST http://localhost:8000/api/orders \
  -H "Content-Type: application/json" \
  -d '{...}'

# Obtener estado
curl http://localhost:8000/api/orders/1
```

### Ver todas las rutas disponibles
```bash
# Abre en navegador
http://localhost:8000/openapi.json
```

---

## 🔐 SEGURIDAD EN PRODUCCIÓN

### 1. Actualizar .env
```
DEBUG=False              # ⚠️ IMPORTANTE
FRONTEND_URL=https://mi-dominio.com
```

### 2. HTTPS
- Usar certificado SSL/TLS
- Redireccionar HTTP a HTTPS

### 3. CORS Restrictivo
```python
allow_origins=[
    "https://mi-dominio.com",  # Solo tu dominio
    "https://www.mi-dominio.com"
]
```

### 4. Variables de Entorno
- NUNCA commitear `.env`
- Usar secrets en el servidor (GitHub Actions, etc)
- Rotar credenciales regularmente

### 5. Rate Limiting
```bash
pip install slowapi
```

```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter

@app.post("/api/pay")
@limiter.limit("5/minute")
def procesar_pago(...):
    ...
```

### 6. Validación de Entrada
- Pydantic valida automáticamente
- Pero verifica montos > 0
- Valida que usuario no abuse

---

## 📈 OPTIMIZACIONES

### 1. Caché de Productos
```python
from functools import lru_cache

@lru_cache(maxsize=128)
def obtener_producto_cache(producto_id):
    ...
```

### 2. Conexión a BD
```python
# Aumentar pool size si hay muchas peticiones
engine = create_engine(
    DATABASE_URL,
    pool_size=20,  # Aumentar si es necesario
    max_overflow=40
)
```

### 3. Async
```python
# Para operaciones lentas, usar async
import asyncio

@app.post("/api/pay")
async def procesar_pago_async(...):
    # Operación de larga duración en background
    ...
```

---

## 📞 CONTACTO Y RECURSOS

- **FastAPI Docs:** https://fastapi.tiangolo.com/
- **Supabase Docs:** https://supabase.com/docs
- **Culqi Docs:** https://culqi.com/documentacion
- **SQLAlchemy:** https://docs.sqlalchemy.org/

---

## ✅ CHECKLIST ANTES DE PRODUCCIÓN

- [ ] `.env` con credenciales reales
- [ ] DEBUG=False
- [ ] CORS restrictivo a tu dominio
- [ ] HTTPS habilitado
- [ ] Base de datos respaldada
- [ ] Logs siendo monitoreados
- [ ] Plan de recuperación ante errores
- [ ] Pruebas de carga ejecutadas
- [ ] Validación de montos en Culqi
- [ ] Almacenamiento seguro de credenciales

---

**¡Listo para producción! 🚀**