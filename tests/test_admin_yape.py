"""Panel de conciliacion de Yape: solo admins, y cada cobro con su pedido. Sin red."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

import routes_admin_yape  # noqa: E402
import services_yape_propio  # noqa: E402
from config import settings  # noqa: E402
from main import app  # noqa: E402
from services_supabase_auth import SupabaseAuthError  # noqa: E402

ORDER_ID = "6f1c2a0e-1111-4c4c-9999-000000000002"
CLIENTE_ID = "7a2b3c4d-1111-4c4c-9999-000000000009"
MERCHANT_ID = "9c8b7a6d-1111-4c4c-9999-000000000001"
PERIODO = {"desde": "2026-10-26T00:00:00-05:00", "hasta": "2026-10-27T00:00:00-05:00"}
TOKEN = {"Authorization": "Bearer token-valido"}


@pytest.fixture()
def entorno(monkeypatch):
    monkeypatch.setattr(settings, "yape_propio_url", "https://yape.acme.test")
    monkeypatch.setattr(settings, "yape_propio_api_key", "clave")
    monkeypatch.setattr(settings, "yape_propio_webhook_secreto", "secreto")

    usuarios = {"token-valido": "admin-1", "token-cliente": "cliente-1"}

    def verificar(token):
        if token not in usuarios:
            raise SupabaseAuthError("Sesion invalida o expirada.")
        return usuarios[token]

    tablas = {
        "profiles": [
            {"user_id": "admin-1", "default_role": "customer"},
            {"user_id": "cliente-1", "default_role": "customer"},
            {"user_id": CLIENTE_ID, "full_name": "Rosa Mamani", "email": "rosa@x.pe", "phone": "987000111"},
        ],
        "user_roles": [{"user_id": "admin-1", "role_id": "r-admin"}],
        "roles": [{"id": "r-admin", "code": "admin"}],
        "orders": [{"id": ORDER_ID, "order_code": "A-1042", "status": "delivered", "payment_status": "paid",
                    "total": 15, "currency": "PEN", "customer_id": CLIENTE_ID, "merchant_id": MERCHANT_ID,
                    "created_at": "2026-10-26T12:00:00Z"}],
        "merchants": [{"id": MERCHANT_ID, "trade_name": "Brasas Huanca", "legal_name": "Brasas SAC"}],
    }

    def sb_get(table, params):
        filas = tablas[table]
        for campo, filtro in params.items():
            if campo in ("select", "limit"):
                continue
            op, _, valor = filtro.partition(".")
            valores = valor.strip("()").split(",") if op == "in" else [valor]
            filas = [f for f in filas if str(f.get(campo)) in valores]
        return filas

    pedidos: list[tuple[str, str]] = []

    def traer(desde, hasta):
        pedidos.append((desde, hasta))
        return {
            "yapeos": [{"id": "y1", "monto_centimos": 1500, "veredicto": "conciliado", "sesion_id": "s1"}],
            "cobros": [{"id": "s1", "referencia": ORDER_ID, "cliente_id": CLIENTE_ID, "veredicto": "conciliado"},
                       {"id": "s2", "referencia": "grupo-raro", "cliente_id": None, "veredicto": "sin_pago"}],
            "devoluciones": [], "reclamos": [], "capturas": [], "receptores": [],
        }

    monkeypatch.setattr(routes_admin_yape, "verify_supabase_user", verificar)
    monkeypatch.setattr(routes_admin_yape, "_sb_get", sb_get)
    monkeypatch.setattr(services_yape_propio, "conciliacion", traer)
    return TestClient(app), tablas, pedidos


def test_admin_ve_cada_cobro_con_su_pedido(entorno):
    cliente, _, pedidos = entorno
    r = cliente.get("/api/admin/yape/conciliacion", params=PERIODO, headers=TOKEN)
    assert r.status_code == 200
    datos = r.json()
    pedido = datos["pedidos"][ORDER_ID]
    assert pedido["codigo"] == "A-1042" and pedido["comercio"] == "Brasas Huanca"
    assert pedido["cliente_nombre"] == "Rosa Mamani" and pedido["cliente_celular"] == "987000111"
    assert datos["clientes"][CLIENTE_ID]["nombre"] == "Rosa Mamani"
    # Una referencia que no es un pedido de ACME no rompe nada.
    assert "grupo-raro" not in datos["pedidos"]
    assert pedidos == [("2026-10-26T00:00:00-05:00", "2026-10-27T00:00:00-05:00")]


def test_admin_por_default_role_tambien_entra(entorno):
    cliente, tablas, _ = entorno
    tablas["user_roles"].clear()
    tablas["profiles"][0]["default_role"] = "super_admin"
    assert cliente.get("/api/admin/yape/conciliacion", params=PERIODO, headers=TOKEN).status_code == 200


def test_sin_sesion_o_sin_rol_no_ve_nada(entorno):
    cliente, _, pedidos = entorno
    assert cliente.get("/api/admin/yape/conciliacion", params=PERIODO).status_code == 401
    r = cliente.get("/api/admin/yape/conciliacion", params=PERIODO, headers={"Authorization": "Bearer inventado"})
    assert r.status_code == 401
    r = cliente.get("/api/admin/yape/conciliacion", params=PERIODO, headers={"Authorization": "Bearer token-cliente"})
    assert r.status_code == 403
    assert pedidos == []
