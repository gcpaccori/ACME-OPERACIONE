"""
Pruebas de la integracion con el SDK de Izipay (tarjeta, Yape, Plin, QR).

Corren sin red: la firma se arma con una clave hash de prueba siguiendo la
documentacion (HMAC-SHA-256 de payloadHttp, en base64).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

import routes_courier_payments  # noqa: E402
import routes_webhooks  # noqa: E402
from config import settings  # noqa: E402
from main import app  # noqa: E402
from services_izipay_sdk import IzipaySdkService, izipay_order_number, summarize_payload  # noqa: E402

HASH_KEY = "clave-hash-de-prueba"
ORDER_ID = "6f1c2a0e-1111-4c4c-9999-000000000001"


def sign(text: str, key: str = HASH_KEY) -> str:
    return base64.b64encode(hmac.new(key.encode(), text.encode(), hashlib.sha256).digest()).decode()


def sdk_payload(order_id: str = ORDER_ID, amount: str = "2.50", code: str = "00", pay_method: str = "YAPE_CODE") -> dict:
    """Forma de la respuesta del SDK y de la IPN (developers.izipay.pe/web-core/notifications)."""
    return {
        "code": code,
        "message": "OK" if code == "00" else "Denegado",
        "messageUser": "Operación exitosa" if code == "00" else "Operación denegada",
        "messageUserEng": "Successful",
        "transactionId": "abc123def456",
        "response": {
            "payMethod": pay_method,
            "order": [
                {
                    "payMethodAuthorization": pay_method,
                    "codeAuth": "831000",
                    "currency": "PEN",
                    "amount": amount,
                    "orderNumber": izipay_order_number(order_id),
                    "stateMessage": "Autorizado" if code == "00" else "Denegado",
                    "uniqueId": "1429386",
                    "referenceNumber": "6330605",
                }
            ],
            "customFields": [{"name": "field1", "value": order_id}],
        },
    }


@pytest.fixture(autouse=True)
def sdk_keys(monkeypatch):
    monkeypatch.setattr(settings, "izipay_sdk_merchant_code", "4001234")
    monkeypatch.setattr(settings, "izipay_sdk_api_key", "api-key-de-prueba")
    monkeypatch.setattr(settings, "izipay_sdk_hash_key", HASH_KEY)
    monkeypatch.setattr(settings, "izipay_sdk_rsa_public_key", "-----BEGIN PUBLIC KEY-----x")
    monkeypatch.setattr(settings, "izipay_sdk_env", "sandbox")


@pytest.fixture
def applied(monkeypatch):
    calls: list[tuple] = []

    def fake_apply(event_type, new_status, verified, obj=None, provider="culqi"):
        calls.append((event_type, new_status, verified, provider))
        return new_status

    monkeypatch.setattr(routes_webhooks, "apply_verified_status", fake_apply)
    return calls


def test_numero_de_pedido_cabe_en_izipay():
    number = izipay_order_number(ORDER_ID)
    assert 5 <= len(number) <= 15
    assert number.isalnum()
    assert number == izipay_order_number(ORDER_ID)


def test_firma():
    service = IzipaySdkService()
    text = json.dumps(sdk_payload())
    assert service.verificar_firma(text, sign(text))
    assert not service.verificar_firma(text, sign(text, "otra-clave"))
    assert not service.verificar_firma(text + " ", sign(text))
    assert service.leer_firmado(text, sign(text))["code"] == "00"
    assert service.leer_firmado(text, "firma-falsa") is None


def test_resumen_pagado_y_rechazado():
    resumen = summarize_payload(sdk_payload())
    assert resumen["status"] == "paid"
    assert resumen["amount"] == 250
    assert resumen["courier_order_id"] == ORDER_ID
    assert resumen["pay_method"] == "YAPE_CODE"
    assert summarize_payload(sdk_payload(code="05"))["status"] == "failed"
    # Error de comunicacion: no se sabe si se cobro, no se marca nada.
    assert summarize_payload(sdk_payload(code="021"))["status"] is None


def test_ipn_valida_aplica_pago(applied):
    text = json.dumps(sdk_payload())
    res = TestClient(app).post(
        "/api/webhooks/izipay-sdk",
        json={"code": "00", "payloadHttp": text, "signature": sign(text), "transactionId": "abc123def456"},
    )
    assert res.status_code == 200
    assert len(applied) == 1
    _, status, verified, provider = applied[0]
    assert (status, provider) == ("paid", "izipay_sdk")
    assert verified["amount"] == 250
    assert verified["metadata"]["pedido_id"] == ORDER_ID
    assert verified["id"] == izipay_order_number(ORDER_ID)


def test_ipn_con_firma_falsa_no_aplica(applied):
    text = json.dumps(sdk_payload())
    res = TestClient(app).post(
        "/api/webhooks/izipay-sdk",
        json={"payloadHttp": text, "signature": sign(text, "otra-clave")},
    )
    assert res.status_code == 400
    assert applied == []


def test_ipn_no_confia_en_campos_sin_firmar(applied):
    # El code de afuera dice 00, pero el payload firmado dice rechazado.
    text = json.dumps(sdk_payload(code="05"))
    TestClient(app).post(
        "/api/webhooks/izipay-sdk",
        json={"code": "00", "payloadHttp": text, "signature": sign(text)},
    )
    assert applied and applied[0][1] == "failed"


def test_confirmacion_del_navegador(applied):
    text = json.dumps(sdk_payload())
    res = TestClient(app).post(
        "/api/courier/payments/izipay-sdk/confirm",
        json={"order_id": ORDER_ID, "payload_http": text, "signature": sign(text)},
    )
    assert res.status_code == 200, res.text
    assert res.json()["payment_status"] == "paid"
    assert applied and applied[0][1] == "paid"


def test_confirmacion_de_otro_pedido_se_rechaza(applied):
    text = json.dumps(sdk_payload(order_id="7a2d3b1f-2222-4d4d-8888-000000000002"))
    res = TestClient(app).post(
        "/api/courier/payments/izipay-sdk/confirm",
        json={"order_id": ORDER_ID, "payload_http": text, "signature": sign(text)},
    )
    assert res.status_code == 400
    assert applied == []


def test_proveedor_activo_prefiere_sdk(monkeypatch):
    from config import active_payment_provider

    monkeypatch.setattr(settings, "payment_provider", "")
    assert active_payment_provider() == "izipay_sdk"
    monkeypatch.setattr(settings, "payment_provider", "culqi")
    assert active_payment_provider() == "culqi"
    monkeypatch.setattr(settings, "payment_provider", "")
    monkeypatch.setattr(settings, "izipay_sdk_hash_key", "")
    assert active_payment_provider() != "izipay_sdk"


def test_checkout_config(monkeypatch):
    service = IzipaySdkService()
    monkeypatch.setattr(service, "generar_token", lambda **kwargs: "token-de-sesion")
    result = service.crear_checkout(
        courier_order_id=ORDER_ID,
        monto=250,
        email="Cliente@Acme.pe",
        nombre="José Pérez-Gómez",
        telefono="+51 987 654 321",
        cliente_id="user-1",
    )
    assert result["exito"], result
    config = result["config"]
    assert config["order"]["amount"] == "2.50"
    assert config["order"]["orderNumber"] == izipay_order_number(ORDER_ID)
    assert config["transactionId"] == result["transaction_id"]
    assert len(config["order"]["dateTimeTransaction"]) == 16
    assert config["billing"]["firstName"] == "José"
    assert config["billing"]["lastName"] == "Pérez Gómez"
    assert config["billing"]["phoneNumber"] == "987654321"
    assert config["customFields"] == [{"name": "field1", "value": ORDER_ID}]
    assert "YAPE_CODE" in config["order"]["payMethod"]
    assert result["script_url"].startswith("https://sandbox-checkout.izipay.pe/")


def test_order_endpoint_usa_sdk(monkeypatch):
    """El endpoint /order abre el checkout del SDK sin el minimo de S/ 6 de Culqi."""

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

    class FakeSdk:
        configured = True

        def crear_checkout(self, **kwargs):
            assert kwargs["monto"] == 250
            return {
                "exito": True,
                "order_number": izipay_order_number(ORDER_ID),
                "transaction_id": "tx1",
                "authorization": "tok",
                "key_rsa": "rsa",
                "script_url": "https://sandbox-checkout.izipay.pe/payments/v1/js/index.js",
                "config": {"transactionId": "tx1"},
                "mode": "TEST",
                "mensaje": "ok",
            }

    monkeypatch.setattr(settings, "payment_provider", "izipay_sdk")
    monkeypatch.setattr(routes_courier_payments, "SupabaseCourierPayments", lambda: fake)
    monkeypatch.setattr(routes_courier_payments, "verify_supabase_user", lambda token: "user-1")
    monkeypatch.setattr(routes_courier_payments, "sync_izipay_sdk_order", lambda order_id: None)
    monkeypatch.setattr(routes_courier_payments, "izipay_sdk_service", lambda: FakeSdk())

    res = TestClient(app).post(
        "/api/courier/payments/order",
        json={"order_id": ORDER_ID},
        headers={"Authorization": "Bearer t"},
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["provider"] == "izipay_sdk"
    assert data["izipay_checkout"]["authorization"] == "tok"
    assert data["izipay_checkout"]["config"] == {"transactionId": "tx1"}
    assert fake.upserts[0]["provider"] == "izipay_sdk"
    assert fake.upserts[0]["external_reference"] == izipay_order_number(ORDER_ID)
