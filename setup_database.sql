-- Script SQL para crear tablas en Supabase
-- Ejecutar esto en la consola SQL de Supabase

-- 1. CREAR TABLA PRODUCTOS
CREATE TABLE IF NOT EXISTS public.productos (
    id SERIAL PRIMARY KEY,
    nombre VARCHAR(255) NOT NULL,
    descripcion TEXT,
    precio NUMERIC(10, 2) NOT NULL,
    stock INTEGER DEFAULT 0,
    sku VARCHAR(100) UNIQUE NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Índices para productos
CREATE INDEX IF NOT EXISTS idx_productos_sku ON public.productos(sku);
CREATE INDEX IF NOT EXISTS idx_productos_stock ON public.productos(stock);

-- 2. CREAR TABLA CLIENTES
CREATE TABLE IF NOT EXISTS public.clientes (
    id SERIAL PRIMARY KEY,
    nombre VARCHAR(255) NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    telefono VARCHAR(20),
    direccion VARCHAR(500) NOT NULL,
    ciudad VARCHAR(100) NOT NULL,
    pais VARCHAR(2) DEFAULT 'PE',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Índices para clientes
CREATE INDEX IF NOT EXISTS idx_clientes_email ON public.clientes(email);

-- 3. CREAR TABLA PEDIDOS
CREATE TABLE IF NOT EXISTS public.pedidos (
    id SERIAL PRIMARY KEY,
    cliente_id INTEGER NOT NULL REFERENCES public.clientes(id),
    estado VARCHAR(50) DEFAULT 'pendiente',
    total NUMERIC(10, 2) DEFAULT 0.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Índices para pedidos
CREATE INDEX IF NOT EXISTS idx_pedidos_cliente_id ON public.pedidos(cliente_id);
CREATE INDEX IF NOT EXISTS idx_pedidos_estado ON public.pedidos(estado);
CREATE INDEX IF NOT EXISTS idx_pedidos_created_at ON public.pedidos(created_at);

-- 4. CREAR TABLA ITEMS_PEDIDO
CREATE TABLE IF NOT EXISTS public.items_pedido (
    id SERIAL PRIMARY KEY,
    pedido_id INTEGER NOT NULL REFERENCES public.pedidos(id) ON DELETE CASCADE,
    producto_id INTEGER NOT NULL REFERENCES public.productos(id),
    cantidad INTEGER NOT NULL,
    precio_unitario NUMERIC(10, 2) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Índices para items_pedido
CREATE INDEX IF NOT EXISTS idx_items_pedido_pedido_id ON public.items_pedido(pedido_id);
CREATE INDEX IF NOT EXISTS idx_items_pedido_producto_id ON public.items_pedido(producto_id);

-- 5. CREAR TABLA TRANSACCIONES
CREATE TABLE IF NOT EXISTS public.transacciones (
    id SERIAL PRIMARY KEY,
    pedido_id INTEGER NOT NULL UNIQUE REFERENCES public.pedidos(id),
    transaccion_id VARCHAR(100) UNIQUE NOT NULL,
    monto NUMERIC(10, 2) NOT NULL,
    estado VARCHAR(50) DEFAULT 'pending',
    referencia VARCHAR(255),
    mensaje TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Índices para transacciones
CREATE INDEX IF NOT EXISTS idx_transacciones_transaccion_id ON public.transacciones(transaccion_id);
CREATE INDEX IF NOT EXISTS idx_transacciones_pedido_id ON public.transacciones(pedido_id);
CREATE INDEX IF NOT EXISTS idx_transacciones_estado ON public.transacciones(estado);

-- 6. INSERTAR PRODUCTOS DE EJEMPLO
INSERT INTO public.productos (nombre, descripcion, precio, stock, sku) VALUES
    ('Laptop HP Pavilion', 'Laptop de 15.6 pulgadas, Intel i5, 8GB RAM', 1299.99, 10, 'LAP-HP-001'),
    ('Mouse Logitech MX Master', 'Mouse inalámbrico de alta precisión', 99.99, 25, 'MOU-LOG-001'),
    ('Teclado Mecánico RGB', 'Teclado mecánico con iluminación RGB', 149.99, 15, 'KEY-RGB-001'),
    ('Monitor LG 27 pulgadas', 'Monitor IPS 4K 27 pulgadas', 399.99, 8, 'MON-LG-001'),
    ('Webcam Logitech HD', 'Cámara web Full HD 1080p', 79.99, 20, 'WEB-LOG-001')
ON CONFLICT (sku) DO NOTHING;

-- 7. VERIFICAR DATOS
SELECT 'Tablas creadas exitosamente!' as mensaje;
SELECT COUNT(*) as cantidad_productos FROM public.productos;

-- Fin del script
