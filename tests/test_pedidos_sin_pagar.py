"""Pedidos sin pagar: el cliente los cancela, vencen al sincronizar y reviven si el pago llega tarde. Sin red."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

import routes_courier_payments  # noqa: E402
import routes_webhooks  # noqa: E402
import services_yape_propio  # noqa: E402
from services_courier_payments import SupabaseCourierError  # noqa: E402
from main import app  # noqa: E402

ORDER_ID = "6f1c2a0e-1111-4c4c-9999-000000000002"
SESION_ID = "8d0b3c55-2222-4c4c-9999-000000000003"
ITEMS = [{"product_id": "p-1", "product_name": "Pollo", "quantity": 2}]


class FakeSupabase:
    def __init__(self, status="pending_payment", payment_status="pending", yape=False, expira=0):
        self.order = {"id": ORDER_ID, "customer_id": "user-1", "total": 25.5, "status": status,
                      "payment_status": payment_status}
        self.yape = yape
        self.expira = expira
        self.cancelados = []
        self.vencidos = []

    def get_order(self, order_id, token):
        return dict(self.order)

    def find_pending_payment(self, order_id, token, provider="culqi"):
        if provider == "yape_propio" and self.yape:
            return {"id": "pay-1", "external_reference": SESION_ID}
        return None

    def cancel_unpaid_order(self, order_id, reason=None):
        if self.order["payment_status"] == "paid":
            raise SupabaseCourierError('Supabase rpc devolvio 400: "Este pedido ya esta pagado"')
        self.cancelados.append((order_id, reason))
        return {"order_id": order_id, "status": "cancelled", "items": ITEMS}

    def expire_unpaid_order(self, order_id):
        self.vencidos.append(order_id)
        return self.expira


@pytest.fixture()
def cliente(monkeypatch):
    def armar(fake: FakeSupabase, sesion: dict | None = None, duenio: str = "user-1"):
        monkeypatch.setattr(routes_courier_payments, "SupabaseCourierPayments", lambda: fake)
        monkeypatch.setattr(routes_courier_payments, "verify_supabase_user", lambda token: duenio)
        # Las pasarelas no tienen el pago.
        monkeypatch.setattr(routes_courier_payments, "_sincronizar_pago",
                            lambda supabase, order, token: str(order.get("payment_status") or "pending"))
        cerradas = []
        monkeypatch.setattr(services_yape_propio, "ver_sesion", lambda sid: sesion or {})
        monkeypatch.setattr(services_yape_propio, "cancelar_sesion", lambda sid: cerradas.append(sid) or {})
        return TestClient(app), cerradas
    return armar


def _cancelar(client):
    return client.post("/api/courier/payments/cancel", json={"order_id": ORDER_ID},
                       headers={"Authorization": "Bearer t"})


def test_cliente_cancela_su_pedido_sin_pagar_y_recibe_sus_productos(cliente):
    fake = FakeSupabase()
    client, _ = cliente(fake)
    res = _cancelar(client)
    assert res.status_code == 200, res.text
    assert res.json() == {"order_id": ORDER_ID, "status": "cancelled", "items": ITEMS}
    assert fake.cancelados == [(ORDER_ID, None)]


def test_solo_el_duenio_cancela(cliente):
    fake = FakeSupabase()
    client, _ = cliente(fake, duenio="otro")
    assert _cancelar(client).status_code == 403
    assert fake.cancelados == []


def test_no_cancela_un_pedido_pagado(cliente):
    fake = FakeSupabase(status="placed", payment_status="paid")
    client, _ = cliente(fake)
    res = _cancelar(client)
    assert res.status_code == 409
    assert fake.cancelados == []


def test_cierra_el_cobro_de_yape_abierto_antes_de_cancelar(cliente):
    fake = FakeSupabase(yape=True)
    client, cerradas = cliente(fake, sesion={"estado": "abierta", "abonado_centimos": 0})
    assert _cancelar(client).status_code == 200
    assert cerradas == [SESION_ID]
    assert fake.cancelados == [(ORDER_ID, None)]


@pytest.mark.parametrize("sesion", [
    {"estado": "pagada"},
    {"estado": "abierta", "declaro_pago": True},
    {"estado": "vencida", "abonado_centimos": 1000},
    {"estado": "abierta", "pago_por_verificar": {"monto_texto": "S/ 25.00"}},
])
def test_no_cancela_si_su_yapeo_llego_o_se_verifica(cliente, sesion):
    fake = FakeSupabase(yape=True)
    client, cerradas = cliente(fake, sesion=sesion)
    res = _cancelar(client)
    assert res.status_code == 409
    assert cerradas == [] and fake.cancelados == []


def test_sin_respuesta_de_yape_no_cancela(cliente, monkeypatch):
    fake = FakeSupabase(yape=True)
    client, _ = cliente(fake)

    def caido(sid):
        raise services_yape_propio.YapePropioError(502, "No pudimos revisar tu pago con Yape. Intenta en un momento.")

    monkeypatch.setattr(services_yape_propio, "ver_sesion", caido)
    res = _cancelar(client)
    assert res.status_code == 502
    assert fake.cancelados == []


def test_sync_vence_el_pedido_cuyo_plazo_paso(cliente):
    fake = FakeSupabase(expira=1)
    client, _ = cliente(fake)
    res = client.post("/api/courier/payments/sync", json={"order_id": ORDER_ID}, headers={"Authorization": "Bearer t"})
    assert res.status_code == 200, res.text
    assert res.json() == {"order_id": ORDER_ID, "payment_status": "pending", "order_status": "cancelled"}
    assert fake.vencidos == [ORDER_ID]


def test_sync_no_vence_un_pedido_pagado(cliente):
    fake = FakeSupabase(status="placed", payment_status="paid", expira=1)
    client, _ = cliente(fake)
    res = client.post("/api/courier/payments/sync", json={"order_id": ORDER_ID}, headers={"Authorization": "Bearer t"})
    assert res.json()["order_status"] == "placed"
    assert fake.vencidos == []


class FakeWebhookPayments:
    def __init__(self, revive: bool):
        self.revive = revive
        self.revividos = []
        self.liberados = []

    def revive_expired_order(self, order_id):
        self.revividos.append(order_id)
        return self.revive

    def release_paid_order(self, order_id, bearer_token=None):
        self.liberados.append(order_id)


@pytest.mark.parametrize("revive", [True, False])
def test_pago_tardio_de_un_pedido_vencido(monkeypatch, revive):
    fake = FakeWebhookPayments(revive)
    patches = []
    monkeypatch.setattr(routes_webhooks, "SupabaseCourierPayments", lambda: fake)
    monkeypatch.setattr(routes_webhooks, "_sb_get", lambda table, params: [
        {"id": ORDER_ID, "total": 25.5, "status": "cancelled", "payment_status": "pending"}] if table == "orders" else [])
    monkeypatch.setattr(routes_webhooks, "_sb_patch", lambda table, params, data: patches.append((table, data)))
    verified = {"id": SESION_ID, "amount": 2550, "metadata": {"pedido_id": ORDER_ID}}
    aplicado = routes_webhooks.apply_verified_status("yape_propio.webhook", "paid", verified, provider="yape_propio")
    assert aplicado == "paid"
    assert fake.revividos == [ORDER_ID]
    # El pago siempre queda registrado; si revivio, release_paid_order lo manda a operaciones.
    assert patches[0] == ("orders", {"payment_status": "paid", "updated_at": patches[0][1]["updated_at"]})
    assert fake.liberados == [ORDER_ID]
