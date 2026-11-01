import copy

import pytest

from config import settings
from services_pricing import (
    PricingConfigError,
    compute_order_pricing,
    default_config,
    merge_config,
    validate_config,
)


@pytest.fixture(autouse=True)
def pasarelas(monkeypatch):
    monkeypatch.setattr(settings, "payment_provider", "izipay")
    monkeypatch.setattr(settings, "culqi_public_key", "pk")
    monkeypatch.setattr(settings, "culqi_private_key", "sk")
    monkeypatch.setattr(settings, "yape_propio_url", "https://yape.test")
    monkeypatch.setattr(settings, "yape_propio_api_key", "k")
    monkeypatch.setattr(settings, "yape_propio_webhook_secreto", "s")
    monkeypatch.setattr(settings, "yape_propio_max_centimos", 0)
    monkeypatch.setattr(settings, "yape_directo_fee_rate", 0.035)


def torta(cfg=None, method="yape_directo", **kw):
    cfg = cfg or default_config()
    return compute_order_pricing(
        cfg,
        lines=[{"line_total": 8.0, "quantity": 1, "merchant_id": "m1", "exempt": False}],
        delivery_fee=7.0,
        payment_method=method,
        **kw,
    )


def con(**cambios):
    cfg = copy.deepcopy(default_config())
    for ruta, valor in cambios.items():
        nodo = cfg
        partes = ruta.split("__")
        for p in partes[:-1]:
            nodo = nodo[p]
        nodo[partes[-1]] = valor
    return validate_config(cfg)


def test_por_defecto_cobra_igual_que_antes():
    # Ejemplo del PDF: torta S/ 8 + envio S/ 7.
    r = torta()
    assert r["service_fee"] == 0.96
    assert r["igv_amount"] == 1.43
    assert r["total"] == 18.02
    assert torta(method="card")["total"] == 18.97
    assert torta(method="yape")["total"] == 20.89


def test_el_cliente_ve_solo_tres_grupos_que_suman_el_total():
    r = torta(tip=2.0)
    grupos = {g["key"]: g["amount"] for g in r["client_groups"]}
    assert set(grupos) == {"producto", "envio", "comision"}
    assert grupos["producto"] == 8.0
    # Envio + su IGV.
    assert grupos["envio"] == round(7 + r["igv_by"]["delivery"], 2)
    assert round(sum(grupos.values()) + r["tip"], 2) == r["total"]


def test_tarifa_sobre_el_envio():
    r = torta(con(service_fee__base=["delivery"], service_fee__tier_by="products"))
    assert r["service_fee"] == 0.84


def test_tarifa_sobre_productos_y_envio():
    r = torta(con(service_fee__base=["products", "delivery"]))
    assert r["service_fee"] == round(15 * 0.12, 2)


def test_tramo_8_por_ciento_sobre_todo_o_escalonado():
    cfg = default_config()
    big = [{"line_total": 150.0, "quantity": 1, "merchant_id": "m1", "exempt": False}]
    todo = compute_order_pricing(cfg, lines=big, delivery_fee=7, payment_method="card")
    assert todo["service_fee"] == 12.0
    esc = compute_order_pricing(con(service_fee__tier_mode="progressive"), lines=big, delivery_fee=7, payment_method="card")
    assert esc["service_fee"] == 16.0  # 12 + 4


def test_limite_de_100_editable():
    cfg = con(service_fee__tiers=[{"up_to": 50, "rate": 0.12}, {"up_to": None, "rate": 0.05}])
    r = compute_order_pricing(cfg, lines=[{"line_total": 60, "quantity": 1}], delivery_fee=5, payment_method="card")
    assert r["service_fee"] == 3.0


def test_igv_incluido_o_sin_igv():
    inc = torta(con(igv__mode="included"))
    assert inc["igv_added"] is False
    assert inc["total"] < torta()["total"]
    assert inc["igv_amount"] == round(7.96 - 7.96 / 1.18, 2)
    nada = torta(con(igv__mode="none"))
    assert nada["igv_amount"] == 0.0


def test_igv_solo_sobre_la_tarifa():
    r = torta(con(igv__applies_to={"delivery": False, "multi_store": False, "service_fee": True, "product_markup": False}))
    assert r["igv_amount"] == round(0.96 * 0.18, 2)


def test_condicion_en_productos_de_un_negocio():
    cfg = con(merchant_rules=[{"merchant_id": "m1", "name": "Pollos", "markup_rate": 0.10, "service_fee_exempt": True}])
    r = torta(cfg)
    assert r["product_markup"] == 0.8
    assert r["service_fee"] == 0.0
    grupos = {g["key"]: g["amount"] for g in r["client_groups"]}
    # La condicion va dentro de "Productos" con su IGV; el local recibe S/ 8.
    assert grupos["producto"] == round(8.8 + r["igv_by"]["product_markup"], 2)
    assert r["payout"][0] == {"to": "Local", "amount": 8.0}


def test_tarifa_propia_de_un_negocio():
    cfg = con(merchant_rules=[{"merchant_id": "m1", "service_fee_rate": 0.05}])
    assert torta(cfg)["service_fee"] == 0.4


def test_bebidas_acme_y_pedido_solo_de_bebidas():
    cfg = default_config()
    mixto = compute_order_pricing(cfg, lines=[
        {"line_total": 90, "quantity": 1}, {"line_total": 60, "quantity": 1, "exempt": True},
    ], delivery_fee=7, payment_method="card")
    assert mixto["service_fee"] == 10.8
    solo = compute_order_pricing(cfg, lines=[{"line_total": 150, "quantity": 1, "exempt": True}], delivery_fee=7, payment_method="card")
    assert solo["service_fee"] == 12.0


def test_recargo_por_locales_y_tope():
    r = torta(branch_count=3)
    assert r["multi_store_surcharge"] == round(7 * 0.25, 2)
    tope = torta(con(multi_store__max_amount=1.0), branch_count=3)
    assert tope["multi_store_surcharge"] == 1.0
    with pytest.raises(ValueError):
        torta(branch_count=6)


def test_pasarela_absorbe_o_porcentaje_propio():
    absorbe = torta(con(gateways__izipay={"mode": "absorb", "rate": 0, "fixed": 0}), method="card")
    assert absorbe["gateway_fee"] == 0.0
    assert absorbe["acme_gateway_margin"] < 0  # ACME paga a Izipay
    propio = torta(con(gateways__izipay={"mode": "percent", "rate": 0.05, "fixed": 0}), method="card")
    assert round(propio["gateway_fee"] / propio["total"], 3) == 0.05


def test_comision_de_pago_en_otro_grupo():
    r = torta(con(gateways__group="producto"))
    grupos = {g["key"]: g["amount"] for g in r["client_groups"]}
    assert grupos["producto"] == round(8 + r["gateway_fee"], 2)


def test_validacion_con_mensajes_claros():
    with pytest.raises(PricingConfigError):
        validate_config(merge_config({"service_fee": {"tiers": [{"up_to": 100, "rate": 2}, {"up_to": None, "rate": 0.08}]}}))
    with pytest.raises(PricingConfigError):
        validate_config(merge_config({"igv": {"mode": "raro"}}))
    with pytest.raises(PricingConfigError):
        validate_config(merge_config({"service_fee": {"tiers": [{"up_to": 100, "rate": 0.1}, {"up_to": 50, "rate": 0.1}, {"up_to": None, "rate": 0}]}}))


def test_simulador_admin_con_envio_a_mano():
    from routes_admin_pricing import SimulateRequest, simulate

    out = simulate(SimulateRequest(items=[{"price": 8}], delivery_fee=7))
    totales = {r["method"]: r["total"] for r in out["results"]}
    assert totales == {"yape_directo": 18.02, "card": 18.97, "yape": 20.89}


def test_simulador_con_mapa_usa_los_precios_editados(monkeypatch):
    import routes_admin_pricing as rap

    monkeypatch.setattr(rap, "calculate_road_distance_km", lambda *a: 1.0)
    cfg = default_config()
    punto = cfg["delivery"]["sector_pricing"]["points"][0]
    cfg["delivery"]["sector_pricing"]["points"][0]["fee"] = 20
    out = rap.simulate(rap.SimulateRequest(
        config=cfg,
        items=[{"price": 8}],
        origin={"lat": -12.7862, "lng": -74.9709},
        destination={"lat": punto["lat"], "lng": punto["lng"]},
    ))
    assert out["delivery"]["source"] == "map"
    assert out["delivery"]["fee"] == 20.0
