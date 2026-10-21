import pytest

from config import settings
from services_courier_quote import calculate_payment_processing_fee, calculate_service_fee


def test_tarifa_por_tramos():
    assert calculate_service_fee(50) == 6.0
    assert calculate_service_fee(100) == 12.0
    assert calculate_service_fee(150) == 12.0
    assert calculate_service_fee(100.01) == 8.0


def test_comision_tarjeta_izipay_y_yape_culqi(monkeypatch):
    monkeypatch.setattr(settings, "payment_provider", "izipay")
    monkeypatch.setattr(settings, "culqi_public_key", "pk_live_x")
    monkeypatch.setattr(settings, "culqi_private_key", "sk_live_x")

    card = calculate_payment_processing_fee(19.5, "card")
    assert card["provider"] == "izipay"
    # 3.44% + IGV y S/ 0.69 + IGV fijo.
    assert card["fee"] == 1.67

    # Yape por Culqi: en compras chicas manda el minimo por pago.
    yape = calculate_payment_processing_fee(19.5, "yape")
    assert yape["provider"] == "culqi"
    assert yape["fee"] == 3.5
    assert yape["tax_amount"] == 0.0
    # En compras grandes manda el porcentaje, sin IGV porque Culqi la publica inafecta.
    assert calculate_payment_processing_fee(200, "yape")["fee"] == 7.13


def test_yape_sin_culqi_no_se_cotiza(monkeypatch):
    monkeypatch.setattr(settings, "payment_provider", "izipay")
    monkeypatch.setattr(settings, "culqi_private_key", "")
    with pytest.raises(ValueError):
        calculate_payment_processing_fee(20, "yape")


def test_recargo_por_locales_extra():
    from services_courier_quote import calculate_multi_store_surcharge

    assert calculate_multi_store_surcharge(6.0, 1) == (0.0, 0.0)
    assert calculate_multi_store_surcharge(6.0, 2) == (0.6, 0.1)
    assert calculate_multi_store_surcharge(6.0, 3) == (1.5, 0.25)
    assert calculate_multi_store_surcharge(6.0, 5) == (4.2, 0.7)
    with pytest.raises(ValueError):
        calculate_multi_store_surcharge(6.0, 6)


def test_pago_libera_los_pedidos_de_los_otros_locales(monkeypatch):
    from services_courier_payments import SupabaseCourierPayments

    calls = []

    def fake_request(self, method, table, bearer_token=None, params=None, json=None, prefer=None):
        calls.append((method, table, dict(params or {}), json))
        if method == "GET" and params.get("id") == "eq.main":
            return [{"id": "main", "order_group_id": "g1", "payment_method_id": "pm"}]
        if method == "GET":
            return [{"id": "extra", "payment_status": "pending"}]
        if method == "PATCH" and table == "orders" and params.get("status") == "eq.pending_payment":
            return [{"id": params["id"][3:]}]
        return []

    monkeypatch.setattr(SupabaseCourierPayments, "__init__", lambda self: None)
    monkeypatch.setattr(SupabaseCourierPayments, "_request", fake_request)
    SupabaseCourierPayments().release_paid_order("main")

    paid = [c for c in calls if c[0] == "PATCH" and c[3] and c[3].get("payment_status") == "paid"]
    placed = [c[2]["id"] for c in calls if c[0] == "PATCH" and c[2].get("status") == "eq.pending_payment"]
    assert paid and paid[0][2]["id"] == "eq.extra"
    assert placed == ["eq.main", "eq.extra"]
    group = [c for c in calls if c[1] == "order_groups"]
    assert group and group[0][3]["payment_status"] == "paid"


def test_cotizacion_con_dos_locales(monkeypatch):
    import routes_courier_quote as rq
    from models import CourierQuoteRequest, QuoteItemInput

    monkeypatch.setattr(settings, "payment_provider", "izipay")
    monkeypatch.setattr(rq, "verify_supabase_user", lambda token: "cliente")
    monkeypatch.setattr(rq, "calculate_road_distance_km", lambda *a: 2.0)
    svc = rq.SupabaseQuoteService
    monkeypatch.setattr(svc, "__init__", lambda self: None)
    monkeypatch.setattr(svc, "get_branch", lambda self, bid, token=None: {"id": bid, "merchant_id": "m-" + bid, "lat": -12.78, "lng": -74.97})
    monkeypatch.setattr(svc, "get_delivery_zone_overrides", lambda self, bid, token=None: {})
    monkeypatch.setattr(svc, "get_products_with_prices", lambda self, ids, token=None: [
        {"id": "p1", "base_price": 20, "name": "Pollo", "merchant_id": "m-b1"},
        {"id": "p2", "base_price": 10, "name": "Jugo", "merchant_id": "m-b2"},
    ])
    saved = {}
    monkeypatch.setattr(svc, "save_quote", lambda self, **kw: saved.update(kw) or {"id": "q1", "expires_at": "x"})

    res = rq.create_courier_quote(
        CourierQuoteRequest(
            branch_id="b1",
            latitude=-12.79,
            longitude=-74.98,
            items=[QuoteItemInput(product_id="p1", quantity=1), QuoteItemInput(product_id="p2", quantity=1, branch_id="b2")],
        ),
        authorization="Bearer t",
    )
    assert res.branch_count == 2
    assert res.multi_store_surcharge_rate == 0.1
    assert res.multi_store_surcharge == round((res.delivery_fee - res.multi_store_surcharge) * 0.1, 2)
    assert [b.subtotal for b in res.branches] == [20.0, 10.0]
    assert res.service_fee == 3.6
    assert {i["branch_id"] for i in saved["items_snapshot"]} == {"b1", "b2"}


def test_pedido_con_dos_locales_crea_un_pedido_por_local(monkeypatch):
    from services_courier_orders import SupabaseOrderService

    posts = []

    def fake_request(self, method, table, bearer_token=None, params=None, json=None, prefer=None):
        if method == "GET" and table == "merchant_branches":
            bid = params["id"][3:]
            return [{"id": bid, "merchant_id": "m-" + bid}]
        if method == "POST":
            posts.append((table, json))
            if table == "orders":
                return [{**json, "order_code": 7}]
        return []

    monkeypatch.setattr(SupabaseOrderService, "__init__", lambda self: None)
    monkeypatch.setattr(SupabaseOrderService, "_request", fake_request)
    quote = {
        "id": "q1", "branch_id": "b1", "subtotal": 30, "delivery_fee": 6.6, "service_fee": 3.6, "total": 41.5,
        "items_snapshot": [
            {"branch_id": "b1", "product_id": "p1", "quantity": 1, "unit_price": 20, "line_total": 20},
            {"branch_id": "b2", "product_id": "p2", "quantity": 1, "unit_price": 10, "line_total": 10},
        ],
    }
    result = SupabaseOrderService().create_order_from_quote(quote, {"fulfillment_type": "delivery"}, "cliente")

    orders = [j for t, j in posts if t == "orders"]
    groups = [j for t, j in posts if t == "order_groups"]
    # El ancla lleva el envio y los cargos; el otro local solo sus productos.
    assert [(o["branch_id"], o["subtotal"], o["total"], o["group_position"]) for o in orders] == [
        ("b1", 20.0, 31.5, 1), ("b2", 10.0, 10.0, 2),
    ]
    assert groups[0]["total"] == 41.5 and groups[0]["locals_count"] == 2 and groups[0]["surcharge_rate"] == 0.1
    assert all(o["order_group_id"] == groups[0]["id"] for o in orders)
    # order_groups_status_check rechaza "pending_payment": el estado inicial
    # lo pone la tabla.
    assert "status" not in groups[0]
    items = [i for t, j in posts if t == "order_items" for i in j]
    assert {i["product_id"]: i["order_id"] for i in items} == {"p1": orders[0]["id"], "p2": orders[1]["id"]}
    assert result["order_id"] == orders[0]["id"]


def test_el_ancla_cobra_el_total_del_grupo(monkeypatch):
    from services_courier_payments import SupabaseCourierPayments

    def fake_request(self, method, table, bearer_token=None, params=None, json=None, prefer=None):
        if table == "orders":
            return [{"id": "main", "total": 31.5, "order_group_id": "g1"}]
        return [{"total": 41.5}]

    monkeypatch.setattr(SupabaseCourierPayments, "__init__", lambda self: None)
    monkeypatch.setattr(SupabaseCourierPayments, "_request", fake_request)
    order = SupabaseCourierPayments().get_order("main")
    assert order["total"] == 41.5 and order["order_total"] == 31.5


def test_los_otros_locales_no_se_cobran_solos(monkeypatch):
    from services_courier_payments import SupabaseCourierPayments

    def fake_request(self, method, table, bearer_token=None, params=None, json=None, prefer=None):
        return [{"id": "extra", "total": 10.0, "order_group_id": "g1", "group_position": 2}]

    monkeypatch.setattr(SupabaseCourierPayments, "__init__", lambda self: None)
    monkeypatch.setattr(SupabaseCourierPayments, "_request", fake_request)
    assert SupabaseCourierPayments().get_order("extra")["total"] == 0
