#!/bin/bash
# Quick Start Script para ACME Ecommerce API
# Ejecutar: bash setup.sh (en Mac/Linux) o setup.bat (en Windows)

echo "╔════════════════════════════════════════════════════════════════╗"
echo "║                                                                ║"
echo "║         🚀 ACME ECOMMERCE API - QUICK START                   ║"
echo "║                                                                ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""

# Paso 1
echo "📦 PASO 1: Crear entorno virtual..."
python -m venv venv
echo "✅ Entorno virtual creado"
echo ""

# Paso 2
echo "🔧 PASO 2: Activar entorno virtual..."
source venv/bin/activate 2>/dev/null || source venv/Scripts/activate 2>/dev/null || venv\Scripts\activate
echo "✅ Entorno activado"
echo ""

# Paso 3
echo "📥 PASO 3: Instalar dependencias..."
pip install -r requirements.txt
echo "✅ Dependencias instaladas"
echo ""

# Paso 4
echo "📋 PASO 4: Verificar instalación..."
python -c "import fastapi; import culqi; print('✅ Todas las dependencias están correctamente instaladas')"
echo ""

# Paso 5
echo "⚙️  PASO 5: Próximos pasos..."
echo ""
echo "   1. Edita el archivo .env con tus credenciales:"
echo "      - SUPABASE_URL"
echo "      - SUPABASE_KEY"
echo "      - DATABASE_URL"
echo "      - CULQI_PUBLIC_KEY"
echo "      - CULQI_PRIVATE_KEY"
echo ""
echo "   2. Ejecuta el script SQL en Supabase:"
echo "      - Abre Supabase > SQL Editor"
echo "      - Copia setup_database.sql"
echo "      - Ejecuta"
echo ""
echo "   3. Inicia la API:"
echo "      uvicorn main:app --reload"
echo ""
echo "   4. Visita:"
echo "      http://localhost:8000/docs"
echo ""
echo "   5. Lee INICIO_AQUI.md para más detalles"
echo ""

echo "╔════════════════════════════════════════════════════════════════╗"
echo "║                                                                ║"
echo "║            ✅ ¡Setup completado! Ya puedes começar           ║"
echo "║                                                                ║"
echo "╚════════════════════════════════════════════════════════════════╝"
