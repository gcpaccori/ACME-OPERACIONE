"""Pruebas de la integracion con la pasarela Yape propia (acme-yape). Sin red."""
from __future__ import annotations

import hashlib
import hmac
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

import routes_courier_payments  # noqa: E402
import routes_webhooks  # noqa: E402
import services_yape_propio  # noqa: E402
from config import settings, yape_propio_aplica  # noqa: E402
from main import app  # noqa: E402
from services_courier_quote import calculate_payment_processing_fee  # noqa: E402

ORDER_ID = "6f1c2a0e-1111-4c4c-9999-000000000002"
SESION_ID = "8d0b3c55-2222-4c4c-9999-000000000003"
SECRETO = "secreto-webhook"


@pytest.fixture(autouse=True)
def yape_propio_activo(monkeypatch):
    monkeypatch.setattr(settings, "yape_propio_url", "https://yape.acme.test")
    monkeypatch.setattr(settings, "yape_propio_api_key", "clave")
    monkeypatch.setattr(settings, "yape_propio_webhook_secreto", SECRETO)
    monkeypatch.setattr(settings, "yape_propio_max_centimos", 8772)


def firmar(cuerpo: bytes, ts: str | None = None) -> dict[str, str]:
    ts = ts or str(int(time.time()))
    firma = hmac.new(SECRETO.encode(), ts.encode() + b"." + cuerpo, hashlib.sha256).hexdigest()
    return {"X-Yape-Timestamp": ts, "X-Yape-Firma": firma, "Content-Type": "application/json"}


def evento(monto_base: int = 2550, extra: int = 1) -> dict:
    return {
        "evento": "yape.sesion_pagada",
        "sesion_id": SESION_ID,
        "referencia": ORDER_ID,
        "cliente_id": "user-1",
        "monto_centimos": monto_base + extra,
        "monto_base_centimos": monto_base,
        "extra_centimos": extra,
        "metodo": "notificacion",
        "nombre_pagador": "Rosa Mamani Q",
    }


@pytest.fixture()
def applied(monkeypatch):
    calls = []

    def fake_apply(event_type, new_status, verified, obj=None, provider="culqi"):
        calls.append((event_type, new_status, verified, provider))
        return "paid"

    monkeypatch.setattr(routes_webhooks, "apply_verified_status", fake_apply)
    return calls


def test_solo_yape_directo_y_montos_chicos(monkeypatch):
    assert yape_propio_aplica("yape_directo", 8772)
    assert not yape_propio_aplica("yape_directo", 8773)
    assert not yape_propio_aplica("card", 500)
    # "yape" es Yape por Culqi: el metodo propio solo se usa si el cliente lo elige.
    assert not yape_propio_aplica("yape", 500)
    # Sin el secreto del webhook ningun pago se confirmaria: queda apagada.
    monkeypatch.setattr(settings, "yape_propio_webhook_secreto", "")
    assert not yape_propio_aplica("yape_directo", 500)
    monkeypatch.setattr(settings, "yape_propio_webhook_secreto", SECRETO)
    monkeypatch.setattr(settings, "yape_propio_url", "")
    assert not yape_propio_aplica("yape_directo", 500)


def test_cotizacion_sin_comision_hasta_el_limite():
    chico = calculate_payment_processing_fee(25.50, "yape_directo")
    assert chico["fee"] == 0.0 and chico["provider"] == "yape_propio"
    grande = calculate_payment_processing_fee(150.0, "yape_directo")
    assert grande["provider"] != "yape_propio" and grande["fee"] > 0
    assert calculate_payment_processing_fee(25.50, "yape")["provider"] != "yape_propio"
    assert calculate_payment_processing_fee(25.50, "card")["provider"] != "yape_propio"


def test_webhook_firmado_aplica_el_pago_por_el_monto_base(applied):
    cuerpo = json.dumps(evento()).encode()
    res = TestClient(app).post("/api/webhooks/yape-propio", content=cuerpo, headers=firmar(cuerpo))
    assert res.status_code == 200, res.text
    _, status, verified, provider = applied[0]
    assert (status, provider) == ("paid", "yape_propio")
    # Los centimos extra son comision: se compara contra el total del pedido.
    assert verified["amount"] == 2550
    assert verified["metadata"]["pedido_id"] == ORDER_ID
    assert verified["transaction_uuid"] == SESION_ID


def test_webhook_sin_firma_o_vencido_no_aplica(applied):
    cuerpo = json.dumps(evento()).encode()
    cliente = TestClient(app)
    assert cliente.post("/api/webhooks/yape-propio", content=cuerpo).status_code == 401
    malo = firmar(cuerpo)
    malo["X-Yape-Firma"] = "0" * 64
    assert cliente.post("/api/webhooks/yape-propio", content=cuerpo, headers=malo).status_code == 401
    viejo = firmar(cuerpo, ts=str(int(time.time()) - 3600))
    assert cliente.post("/api/webhooks/yape-propio", content=cuerpo, headers=viejo).status_code == 401
    otro = json.dumps(evento(monto_base=9999)).encode()
    assert cliente.post("/api/webhooks/yape-propio", content=otro, headers=firmar(cuerpo)).status_code == 401
    assert applied == []


class FakeSupabase:
    def __init__(self, total: float, quoted_method: str | None = None):
        self.total = total
        self.quoted_method = quoted_method
        self.upserts = []

    def get_quote_payment_method(self, quote_id, token):
        return self.quoted_method

    def get_order(self, order_id, token):
        return {"id": ORDER_ID, "customer_id": "user-1", "total": self.total, "status": "pending_payment", "payment_status": "pending"}

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


def _pedir(monkeypatch, total: float, abrir, method: str = "yape_directo", quoted_method: str | None = None,
           nombre: str | None = None):
    fake = FakeSupabase(total, quoted_method)
    monkeypatch.setattr(routes_courier_payments, "SupabaseCourierPayments", lambda: fake)
    monkeypatch.setattr(routes_courier_payments, "verify_supabase_user", lambda token: "user-1")
    monkeypatch.setattr(services_yape_propio, "abrir_sesion", abrir)
    res = TestClient(app).post(
        "/api/courier/payments/order",
        json={"order_id": ORDER_ID, "payment_method": method, "nombre_pagador_yape": nombre},
        headers={"Authorization": "Bearer t"},
    )
    return res, fake


def test_order_abre_sesion_yape_propio(monkeypatch):
    llamadas = []

    def abrir(referencia, cliente_id, monto, nombre_pagador=None):
        llamadas.append((referencia, cliente_id, monto, nombre_pagador))
        return {"sesion_id": SESION_ID, "token": "tok", "monto_centimos": 2551, "monto_texto": "S/ 25.51",
                "expira_en": "2026-10-22T16:00:00+00:00", "segundos_restantes": 60,
                "yape": {"numero": "987654321", "titular": "ACME", "qr_url": "https://x/qr.png"}}

    res, fake = _pedir(monkeypatch, 25.50, abrir, nombre="Gabriel Ccansaya")
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["provider"] == "yape_propio"
    assert data["monto_centimos"] == 2551
    assert data["yape_propio"]["token"] == "tok"
    assert data["yape_propio"]["api_url"] == "https://yape.acme.test"
    assert llamadas == [(ORDER_ID, "user-1", 2550, "Gabriel Ccansaya")]
    assert fake.upserts[0]["provider"] == "yape_propio"
    assert fake.upserts[0]["external_reference"] == SESION_ID


def test_order_avisa_si_el_receptor_no_esta(monkeypatch):
    def abrir(*a, **k):
        raise services_yape_propio.YapePropioError(503, "El celular que recibe los yapeos no esta conectado.",
                                                   "receptor_fuera_de_linea")

    res, _ = _pedir(monkeypatch, 25.50, abrir)
    assert res.status_code == 503
    assert "celular" in res.json()["detail"]


def test_order_grande_no_usa_yape_propio(monkeypatch):
    def abrir(*a, **k):
        raise AssertionError("no deberia abrir sesion propia")

    monkeypatch.setattr(settings, "payment_provider", "culqi")
    monkeypatch.setattr(routes_courier_payments.culqi_service, "obtener_orden", lambda ref: {})
    monkeypatch.setattr(routes_courier_payments.culqi_service, "crear_orden_checkout",
                        lambda **k: {"exito": True, "order_id": "ord_1", "monto_centimos": 15000})
    res, fake = _pedir(monkeypatch, 150.0, abrir, method="yape")
    assert res.status_code == 200, res.text
    assert res.json()["provider"] == "culqi"
    # Si aun asi pide Yape directo por encima del limite, se le avisa en vez de cobrarle comision.
    res, _ = _pedir(monkeypatch, 150.0, abrir)
    assert res.status_code == 409


def _sesion_ok(*a, **k):
    return {"sesion_id": SESION_ID, "token": "tok", "monto_centimos": 2550, "monto_texto": "S/ 25.50",
            "expira_en": None, "segundos_restantes": 60,
            "yape": {"numero": "987654321", "titular": "ACME", "qr_url": ""}}


def test_cotizado_con_yape_no_se_cobra_con_tarjeta(monkeypatch):
    """Un total sin comision (cotizado con Yape propio) no puede abrirse como cobro con tarjeta."""
    res, fake = _pedir(monkeypatch, 25.50, _sesion_ok, method="card", quoted_method="yape_directo")
    assert res.status_code == 200, res.text
    assert res.json()["provider"] == "yape_propio"


def test_cotizado_con_tarjeta_no_usa_yape_propio(monkeypatch):
    def abrir(*a, **k):
        raise AssertionError("no deberia abrir sesion propia")

    monkeypatch.setattr(settings, "payment_provider", "culqi")
    monkeypatch.setattr(routes_courier_payments.culqi_service, "obtener_orden", lambda ref: {})
    monkeypatch.setattr(routes_courier_payments.culqi_service, "crear_orden_checkout",
                        lambda **k: {"exito": True, "order_id": "ord_1", "monto_centimos": 2550})
    res, _ = _pedir(monkeypatch, 25.50, abrir, method="yape_directo", quoted_method="card")
    assert res.status_code == 200, res.text
    assert res.json()["provider"] == "culqi"


def test_yape_por_culqi_no_usa_yape_propio(monkeypatch):
    def abrir(*a, **k):
        raise AssertionError("no deberia abrir sesion propia")

    monkeypatch.setattr(settings, "payment_provider", "culqi")
    monkeypatch.setattr(routes_courier_payments.culqi_service, "obtener_orden", lambda ref: {})
    monkeypatch.setattr(routes_courier_payments.culqi_service, "crear_orden_checkout",
                        lambda **k: {"exito": True, "order_id": "ord_1", "monto_centimos": 2550})
    res, _ = _pedir(monkeypatch, 25.50, abrir, method="yape")
    assert res.status_code == 200, res.text
    assert res.json()["provider"] == "culqi"


def test_yape_directo_no_disponible_avisa(monkeypatch):
    monkeypatch.setattr(settings, "yape_propio_url", "")
    res, _ = _pedir(monkeypatch, 25.50, _sesion_ok)
    assert res.status_code == 409
    assert "Yape directo" in res.json()["detail"]


def test_reembolso_yape_propio_se_registra_manual(monkeypatch):
    import routes_admin_refunds

    patches, posts = [], []

    def fake_get(table, params):
        if table == "orders":
            return [{"id": ORDER_ID, "total": 25.5, "payment_status": "paid", "customer_id": "user-1"}]
        if table == "payments":
            assert "yape_propio" in params["provider"]
            return [{"id": "pay-1", "amount": 25.5, "currency": "PEN", "external_reference": SESION_ID,
                     "status": "paid", "provider": "yape_propio"}]
        return []

    monkeypatch.setattr(routes_admin_refunds, "_verify_admin", lambda token: "admin-1")
    monkeypatch.setattr(routes_admin_refunds, "_sb_get", fake_get)
    monkeypatch.setattr(routes_admin_refunds, "_sb_patch", lambda t, p, d: patches.append((t, d)))
    monkeypatch.setattr(routes_admin_refunds, "_sb_post", lambda t, d: posts.append((t, d)))
    res = TestClient(app).post("/api/admin/refunds", json={"order_id": ORDER_ID, "reason": "pedido cancelado"},
                               headers={"Authorization": "Bearer admin"})
    assert res.status_code == 200, res.text
    assert "Yapea S/ 25.50" in res.json()["mensaje"]
    assert patches[0][0] == "payments" and patches[0][1]["status"] == "refunded"
    assert patches[1][0] == "orders" and patches[1][1]["payment_status"] == "refunded"
    assert posts[0][1]["transaction_type"] == "refund"


def _reclamo(monkeypatch, reclamar=None, estado=None, pagado=False, duenio="user-1"):
    fake = FakeSupabase(20.0)
    if pagado:
        fake.get_order = lambda order_id, token: {"id": ORDER_ID, "customer_id": "user-1", "payment_status": "paid"}
    monkeypatch.setattr(routes_courier_payments, "SupabaseCourierPayments", lambda: fake)
    monkeypatch.setattr(routes_courier_payments, "verify_supabase_user", lambda token: duenio)
    if reclamar:
        monkeypatch.setattr(services_yape_propio, "reclamar", reclamar)
    if estado:
        monkeypatch.setattr(services_yape_propio, "estado_reclamo", estado)
    return TestClient(app)


def test_reclamo_lo_hace_solo_el_duenio_del_pedido(monkeypatch):
    llamadas = []
    cliente = _reclamo(monkeypatch, reclamar=lambda *a: llamadas.append(a) or {}, duenio="otro")
    res = cliente.post("/api/courier/payments/yape-propio/reclamo", headers={"Authorization": "Bearer t"},
                       json={"order_id": ORDER_ID, "codigo": "123", "monto_centimos": 2000})
    assert res.status_code == 403 and llamadas == []


def test_reclamo_pasa_codigo_y_monto_a_acme_yape(monkeypatch):
    llamadas = []

    def reclamar(referencia, codigo, monto, ip):
        llamadas.append((referencia, codigo, monto))
        return {"resultado": "aplicado", "mensaje": "Encontramos tu yapeo. Tu pedido ya esta pagado."}

    cliente = _reclamo(monkeypatch, reclamar=reclamar)
    res = cliente.post("/api/courier/payments/yape-propio/reclamo", headers={"Authorization": "Bearer t"},
                       json={"order_id": ORDER_ID, "codigo": "123", "monto_centimos": 2000})
    assert res.status_code == 200 and res.json()["resultado"] == "aplicado"
    assert llamadas == [(ORDER_ID, "123", 2000)]


def test_reclamo_valida_el_codigo_y_no_consulta_si_ya_esta_pagado(monkeypatch):
    cliente = _reclamo(monkeypatch, reclamar=lambda *a: pytest.fail("no debe reclamar"),
                       estado=lambda *a: pytest.fail("no debe consultar"), pagado=True)
    malo = cliente.post("/api/courier/payments/yape-propio/reclamo", headers={"Authorization": "Bearer t"},
                        json={"order_id": ORDER_ID, "codigo": "12a", "monto_centimos": 2000})
    assert malo.status_code == 422
    ok = cliente.post("/api/courier/payments/yape-propio/reclamo", headers={"Authorization": "Bearer t"},
                      json={"order_id": ORDER_ID, "codigo": "123", "monto_centimos": 2000})
    assert ok.json()["resultado"] == "pagada"
    ver = cliente.get("/api/courier/payments/yape-propio/reclamo", params={"order_id": ORDER_ID},
                      headers={"Authorization": "Bearer t"})
    assert ver.json()["estado"] == "pagada"
