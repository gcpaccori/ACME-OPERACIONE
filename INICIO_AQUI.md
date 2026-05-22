# 🚀 INICIO RÁPIDO - ¡COMIENZA AQUÍ!

## 📝 Tu API FastAPI + Culqi fue creada exitosamente

Tienes **TODO** listo para:
- ✅ Vender productos desde tu página web
- ✅ Procesar pagos con Culqi
- ✅ Almacenar datos en Supabase

---

## ⏱️ 3 PASOS PARA EMPEZAR (15 minutos)

### PASO 1: Preparar entorno (5 min)

Abre terminal y ejecuta:

```bash
# 1. Ve a tu carpeta del proyecto
cd C:\Users\ptic252\Documents\GitHub\ACME-OPERACIONE

# 2. Crear entorno virtual
python -m venv venv

# 3. Activar (verás que aparece "venv" al inicio de la línea)
venv\Scripts\activate

# 4. Instalar dependencias (esto toma 1-2 minutos)
pip install -r requirements.txt
```

### PASO 2: Configurar Base de Datos (5 min)

1. Abre tu proyecto en **Supabase** → https://supabase.com
2. Ve a **SQL Editor**
3. Abre el archivo `setup_database.sql` (está en tu carpeta)
4. Copia TODO el contenido
5. Pégalo en Supabase y ejecuta (Ctrl+Enter)
6. Deberías ver: "✅ Tablas creadas exitosamente!"

### PASO 3: Configurar credenciales (5 min)

1. Abre el archivo `.env` en tu editor
2. Completa con tus credenciales:

```
# De Supabase (Project Settings → API)
SUPABASE_URL=https://xxxxx.supabase.co
SUPABASE_KEY=eyJ...
DATABASE_URL=postgresql://postgres:xxx@...

# De Culqi (Dashboard → Integraciones)
CULQI_PUBLIC_KEY=pk_test_xxxxx
CULQI_PRIVATE_KEY=sk_test_xxxxx

# Tu página web
FRONTEND_URL=http://localhost:3000

# Dejar igual
DEBUG=True
```

---

## ▶️ EJECUTAR API

```bash
# Asegúrate de que:
# 1. El venv está activado (ves "venv" al inicio)
# 2. Estás en la carpeta correcta

# Ejecutar:
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

**Resultado esperado:**
```
✅ Base de datos inicializada correctamente
INFO:     Uvicorn running on http://0.0.0.0:8000
```

---

## 🌐 VERIFICAR QUE FUNCIONA

Abre en navegador: **http://localhost:8000/docs**

Deberías ver una interfaz interactiva donde puedes:
- ✅ Ver todos los endpoints
- ✅ Probar GET /api/products
- ✅ Crear un pedido de prueba
- ✅ Ver la documentación

---

## 🔗 INTEGRAR EN TU PÁGINA WEB

1. Abre `ejemplo_frontend.html` en tu editor
2. Busca: `tu_clave_publica_aqui`
3. Reemplaza con tu clave pública de Culqi
4. Cambiar `http://localhost:8000` por tu URL

O ve a **README.md** para ver cómo integrar en JavaScript/React.

---

## 📊 ESTRUCTURA DE CARPETAS

```
Tu proyecto/
├── main.py ........................ ← Ejecutar esto
├── requirements.txt ............... ← Dependencias
├── .env ........................... ← Tus credenciales
├── setup_database.sql ............ ← Ejecutar en Supabase
├── ejemplo_frontend.html ......... ← Tu página web
└── README.md ..................... ← Documentación
```

---

## 📚 DOCUMENTACIÓN

- **RESUMEN.md** - Resumen general
- **README.md** - Guía completa
- **INSTALL.md** - Instalación detallada
- **ESTRUCTURA.md** - Cómo están organizados los archivos
- **TIPS.md** - Troubleshooting y consejos

---

## ❓ PROBLEMAS COMUNES

### ❌ "No module named 'fastapi'"
```bash
# Verifica que venv está activado
# Luego: pip install -r requirements.txt
```

### ❌ "Connection refused" (Supabase)
- Verifica DATABASE_URL en .env
- Cópialo desde Supabase > Project Settings > Database > URI

### ❌ Página web no se conecta a API
- Verifica que FRONTEND_URL en .env es correcto
- Asegúrate que la API está ejecutándose

### ❌ Error "Invalid token" de Culqi
- Verifica que uses credenciales de TEST (pk_test_*)
- Revisa que la clave pública en HTML coincide

---

## 🧪 PROBAR SIN FRONTEND

```bash
# En otra terminal (con venv activado):
python test_api.py
```

Esto probará automáticamente:
- ✅ Conexión a API
- ✅ Listar productos
- ✅ Crear pedido
- ✅ Obtener estado

---

## 🎯 FLUJO COMPLETO

1. **Usuario ve productos** → `GET /api/products`
2. **Usuario crea pedido** → `POST /api/orders`
3. **Usuario paga** → `POST /api/pay` (con token Culqi)
4. **Pedido se marca PAGADO** → Automático
5. **Usuario ve su pedido** → `GET /api/orders/{id}`

---

## 🔐 IMPORTANTE (SEGURIDAD)

⚠️ **NUNCA**:
- Hagas commit del archivo `.env` (contiene credenciales)
- Compartas tus claves de Culqi
- Uses credenciales de producción en desarrollo

✅ **SÍ**:
- Usa `pk_test_*` y `sk_test_*` para pruebas
- Usa `.gitignore` para excluir `.env`
- Cambia a producción solo cuando estés listo

---

## 📞 AYUDA

1. Revisa **TIPS.md** para troubleshooting
2. Revisa logs en la terminal de la API
3. Abre http://localhost:8000/docs para documentación interactiva
4. Revisa la consola del navegador para errores frontend

---

## ✅ CHECKLIST ANTES DE PRODUCCIÓN

- [ ] Todos los archivos creados correctamente
- [ ] Base de datos creada en Supabase
- [ ] .env configurado con tus credenciales
- [ ] API funciona en http://localhost:8000/docs
- [ ] Ejemplo HTML integrado en tu página web
- [ ] Pruebas de pago exitosas
- [ ] DEBUG=False en .env antes de producción

---

## 🎉 ¡ESTÁS LISTO!

Tu API está completamente funcional y lista para procesar compras reales.

**Próximo paso:**
1. Ejecutar: `uvicorn main:app --reload`
2. Abre: http://localhost:8000/docs
3. ¡Comienza a vender!

---

**¿Necesitas ayuda?** Revisa:
- README.md (guía completa)
- TIPS.md (troubleshooting)
- ESTRUCTURA.md (cómo está organizado)

**¡Mucho éxito con tu ecommerce! 🚀**
