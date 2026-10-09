"""
Pruebas de la integracion con Izipay.

Las de firma, resumen y endpoints corren sin red. Las marcadas como
"integracion" hablan con la API de TEST de Izipay y solo corren si las claves
de test estan en el entorno (IZIPAY_USERNAME, IZIPAY_PASSWORD, IZIPAY_PUBLIC_KEY).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import uuid
from pathlib import Path
from urllib.parse import urlencode

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

import routes_courier_payments  # noqa: E402
import routes_webhooks  # noqa: E402
from config import settings  # noqa: E402
from main import app  # noqa: E402
from services_izipay import IzipayService, summarize_answer  # noqa: E402

FAKE_PASSWORD = "testpassword_fake"
FAKE_HMAC = "fake-hmac-key"
ORDER_ID = "6f1c2a0e-1111-4c4c-9999-000000000001"


def sign(key: str, text: str) -> str:
    return hmac.new(key.encode(), text.encode(), hashlib.sha256).hexdigest()


def paid_answer(order_id: str = ORDER_ID, amount: int = 250) -> dict:
    """Forma de la respuesta V4/Payment que Izipay manda al navegador y a la IPN."""
    return {
        "shopId": "12345678",
        "orderCycle": "CLOSED",
        "orderStatus": "PAID",
        "orderDetails": {
            "orderTotalAmount": amount,
            "orderCurrency": "PEN",
            "mode": "TEST",
            "orderId": f"courier-{order_id}",
            "_type": "V4/OrderDetails",
        },
        "transactions": [
            {
                "uuid": "a1b2c3d4e5f6",
                "amount": amount,
                "currency": "PEN",
                "status": "PAID",
                "detailedStatus": "AUTHORISED",
                "operationType": "DEBIT",
                "metadata": {"pedido_id": order_id},
                "_type": "V4/PaymentTransaction",
            }
        ],
        "_type": "V4/Payment",
    }


@pytest.fixture(autouse=True)
def fake_keys(monkeypatch):
    monkeypatch.setattr(settings, "izipay_username", "12345678")
    monkeypatch.setattr(settings, "izipay_password", FAKE_PASSWORD)
    monkeypatch.setattr(settings, "izipay_public_key", "12345678:testpublickey_fake")
    monkeypatch.setattr(settings, "izipay_hmac_key", FAKE_HMAC)


@pytest.fixture
def applied(monkeypatch):
    calls: list[tuple] = []

    def fake_apply(event_type, new_status, verified, obj=None, provider="culqi"):
        calls.append((event_type, new_status, verified, provider))
        return new_status

    monkeypatch.setattr(routes_webhooks, "apply_verified_status", fake_apply)
    return calls


def test_firma_navegador_y_ipn():
    service = IzipayService()
    text = json.dumps(paid_answer())
    assert service.verificar_firma(text, sign(FAKE_HMAC, text), "sha256_hmac")
    assert service.verificar_firma(text, sign(FAKE_PASSWORD, text), "password")
    # La firma de la IPN no vale con la clave HMAC, ni al reves.
    assert not service.verificar_firma(text, sign(FAKE_PASSWORD, text), "sha256_hmac")
    assert not service.verificar_firma(text, sign(FAKE_HMAC, text), "password")
    assert not service.verificar_firma(text + " ", sign(FAKE_HMAC, text), "sha256_hmac")


def test_resumen_pagado():
    resumen = summarize_answer(paid_answer())
    assert resumen["status"] == "paid"
    assert resumen["amount"] == 250
    assert resumen["order_id"] == f"courier-{ORDER_ID}"
    assert resumen["transaction_uuid"] == "a1b2c3d4e5f6"


def test_resumen_order_get_rechazado():
    # Forma real de Order/Get (V4/OrderTransactions) para un intento rechazado.
    answer = {
        "orderId": f"courier-{ORDER_ID}",
        "transactions": [
            {
                "uuid": "x1",
                "amount": 250,
                "currency": "PEN",
                "status": "UNPAID",
                "detailedStatus": "REFUSED",
                "operationType": "DEBIT",
                "orderDetails": {"orderTotalAmount": 250, "orderCurrency": "PEN", "orderId": f"courier-{ORDER_ID}"},
            }
        ],
        "_type": "V4/OrderTransactions",
    }
    resumen = summarize_answer(answer)
    assert resumen["status"] == "failed"
    assert resumen["order_id"] == f"courier-{ORDER_ID}"


def test_resumen_un_rechazo_y_luego_pagado():
    answer = paid_answer()
    answer["transactions"].insert(0, {"uuid": "x1", "amount": 250, "status": "UNPAID", "detailedStatus": "REFUSED"})
    resumen = summarize_answer(answer)
    assert resumen["status"] == "paid"
    assert resumen["transaction_uuid"] == "a1b2c3d4e5f6"


def test_ipn_valida_aplica_pago(applied):
    text = json.dumps(paid_answer())
    body = urlencode({
        "kr-hash": sign(FAKE_PASSWORD, text),
        "kr-hash-algorithm": "sha256_hmac",
        "kr-hash-key": "password",
        "kr-answer-type": "V4/Payment",
        "kr-answer": text,
    })
    res = TestClient(app).post(
        "/api/webhooks/izipay",
        content=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert res.status_code == 200
    assert len(applied) == 1
    _, status, verified, provider = applied[0]
    assert (status, provider) == ("paid", "izipay")
    assert verified["amount"] == 250
    assert verified["metadata"]["pedido_id"] == ORDER_ID


def test_ipn_con_firma_falsa_no_aplica(applied):
    text = json.dumps(paid_answer())
    body = urlencode({"kr-hash": sign("otra-clave", text), "kr-hash-key": "password", "kr-answer": text})
    res = TestClient(app).post(
        "/api/webhooks/izipay",
        content=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert res.status_code == 400
    assert applied == []


def test_confirmacion_del_navegador(applied):
    text = json.dumps(paid_answer())
    res = TestClient(app).post(
        "/api/courier/payments/izipay/confirm",
        json={"order_id": ORDER_ID, "kr_answer": text, "kr_hash": sign(FAKE_HMAC, text), "kr_hash_key": "sha256_hmac"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["payment_status"] == "paid"
    assert applied and applied[0][1] == "paid"


def test_confirmacion_de_otro_pedido_se_rechaza(applied):
    text = json.dumps(paid_answer(order_id="otro-pedido"))
    res = TestClient(app).post(
        "/api/courier/payments/izipay/confirm",
        json={"order_id": ORDER_ID, "kr_answer": text, "kr_hash": sign(FAKE_HMAC, text)},
    )
    assert res.status_code == 400
    assert applied == []


def test_proveedor_activo(monkeypatch):
    from config import active_payment_provider

    monkeypatch.setattr(settings, "payment_provider", "")
    assert active_payment_provider() == "izipay"
    monkeypatch.setattr(settings, "payment_provider", "culqi")
    assert active_payment_provider() == "culqi"
    monkeypatch.setattr(settings, "payment_provider", "")
    monkeypatch.setattr(settings, "izipay_password", "")
    assert active_payment_provider() == "culqi"


live = pytest.mark.skipif(
    not (os.getenv("IZIPAY_USERNAME") and str(os.getenv("IZIPAY_PASSWORD", "")).startswith("testpassword_")),
    reason="Sin claves de TEST de Izipay en el entorno",
)


@live
def test_integracion_form_token_y_order_get(monkeypatch):
    monkeypatch.setattr(settings, "izipay_username", os.environ["IZIPAY_USERNAME"])
    monkeypatch.setattr(settings, "izipay_password", os.environ["IZIPAY_PASSWORD"])
    monkeypatch.setattr(settings, "izipay_public_key", os.environ.get("IZIPAY_PUBLIC_KEY", "x"))
    service = IzipayService()
    pedido = f"prueba-{uuid.uuid4()}"

    # S/ 2.50: un monto que con Culqi no se podia cobrar.
    result = service.crear_form_token(
        courier_order_id=pedido,
        monto=250,
        email="prueba@acmepedidos.com",
        nombre="Cliente de Prueba",
        telefono="987654321",
        referencia_cliente=None,
    )
    assert result["exito"], result
    assert result["mode"] == "TEST"
    assert result["form_token"]

    consulta = service.obtener_orden(f"courier-{pedido}")
    assert consulta["encontrada"] is False


def test_order_endpoint_usa_izipay(monkeypatch):
    """El endpoint /order abre el pago en Izipay sin el minimo de S/ 6 de Culqi."""

    class FakeSupabase:
        def __init__(self):
            self.upserts = []

        def get_order(self, order_id, token):
            return {"id": ORDER_ID, "customer_id": "user-1", "total": 2.5, "status": "pending_payment", "payment_status": "pending"}

        def get_profile(self, *a, **k):
            return {"email": "cliente@acme.pe", "full_name": "Cliente Uno", "phone": "987654321"}

        def get_online_payment_method_id(self, token):
            return "pm-1"

        def find_pending_payment(self, order_id, token, provider="culqi"):
            return None

        def upsert_pending_payment(self, order, **kwargs):
            self.upserts.append(kwargs)
            return "pay-1"

        def update_order_payment(self, *a, **k):
            pass

        def insert_transaction(self, **k):
            pass

    fake = FakeSupabase()

    class FakeIzipay:
        configured = True

        def crear_form_token(self, **kwargs):
            assert kwargs["monto"] == 250
            return {"exito": True, "order_id": f"courier-{ORDER_ID}", "form_token": "tok", "public_key": "pk", "mode": "TEST", "mensaje": "ok"}

    monkeypatch.setattr(settings, "payment_provider", "izipay")
    monkeypatch.setattr(routes_courier_payments, "SupabaseCourierPayments", lambda: fake)
    monkeypatch.setattr(routes_courier_payments, "verify_supabase_user", lambda token: "user-1")
    monkeypatch.setattr(routes_courier_payments, "sync_izipay_order", lambda order_id: None)
    monkeypatch.setattr(routes_courier_payments, "izipay_service", lambda: FakeIzipay())

    res = TestClient(app).post(
        "/api/courier/payments/order",
        json={"order_id": ORDER_ID},
        headers={"Authorization": "Bearer t"},
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["provider"] == "izipay"
    assert data["form_token"] == "tok"
    assert data["monto_centimos"] == 250
    assert fake.upserts[0]["provider"] == "izipay"
