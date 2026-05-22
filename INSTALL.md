# 🚀 GUÍA DE INSTALACIÓN RÁPIDA

## Paso 1: Preparar tu entorno

```bash
# Abrir terminal en la carpeta del proyecto
cd C:\Users\ptic252\Documents\GitHub\ACME-OPERACIONE

# Crear entorno virtual
python -m venv venv

# Activar entorno (Windows)
venv\Scripts\activate

# Instalar dependencias
pip install -r requirements.txt
```

## Paso 2: Configurar Base de Datos en Supabase

### En la consola SQL de Supabase:
1. Ve a tu proyecto en Supabase
2. Abre la consola SQL (SQL Editor)
3. Copia TODO el contenido de `setup_database.sql`
4. Ejecuta (presiona Ctrl+Enter)

**Resultado esperado:** Verás "Tablas creadas exitosamente!" y el número de productos.

## Paso 3: Configurar archivo .env

```bash
# Crear archivo .env (copiando del ejemplo)
copy .env.example .env
```

**Editar `.env` con tus credenciales:**

```
# De Supabase
SUPABASE_URL=https://xxxxx.supabase.co
SUPABASE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
DATABASE_URL=postgresql://postgres:passwordxx@db.xxxxx.supabase.co:5432/postgres

# De Culqi
CULQI_PUBLIC_KEY=pk_test_xxxxx
CULQI_PRIVATE_KEY=sk_test_xxxxx

# URL de tu página web
FRONTEND_URL=http://localhost:3000

# Configuración local
DEBUG=True
```

### ¿Dónde obtener las credenciales?

**Supabase:**
- Project Settings → API → URL y API Key (anon)
- Project Settings → Database → Connection string

**Culqi:**
- Dashboard → Mis Integraciones → Credenciales

## Paso 4: Ejecutar API

```bash
# Asegúrate de que el entorno virtual está activado
# (deberías ver el prefijo "venv" en tu terminal)

# Ejecutar API
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

**Resultado esperado:**
```
✅ Base de datos inicializada correctamente
INFO:     Uvicorn running on http://0.0.0.0:8000
INFO:     Application startup complete
```

## Paso 5: Probar API

Abre en tu navegador:
- **Documentación:** http://localhost:8000/docs
- **Health check:** http://localhost:8000/health

## Paso 6: Integrar con tu página web

1. Abre `ejemplo_frontend.html` en tu editor
2. Busca `tu_clave_publica_aqui` y reemplaza con tu clave pública de Culqi
3. Cambiar `http://localhost:8000` por tu URL de producción
4. Abre el archivo en navegador o intégralo en tu web

## ✅ Checklist de Verificación

- [ ] Venv activado
- [ ] `requirements.txt` instalado
- [ ] `.env` configurado con credenciales
- [ ] Tablas creadas en Supabase
- [ ] API corriendo en http://localhost:8000
- [ ] Documentación accesible en `/docs`
- [ ] Productos visibles en GET `/api/products`
- [ ] Ejemplo HTML funciona en navegador

## 🆘 Troubleshooting

### Error: "No module named 'culqi'"
```bash
pip install culqi==2.2.6
```

### Error: "connection refused" en Supabase
- Verificar `DATABASE_URL` en `.env`
- Copiar desde Supabase: Project Settings → Database → Connection string → URI

### Error: "ModuleNotFoundError: No module named 'email_validator'"
```bash
pip install email-validator
```

### API no responde
- Asegúrate de que el venv está activado
- Verifica que no hay errores en la terminal
- Reinicia con Ctrl+C y `uvicorn main:app --reload`

---

**¡Listo! Tu API está funcionando 🎉**

**Próximo paso:** Abre http://localhost:8000/docs para ver todos los endpoints disponibles