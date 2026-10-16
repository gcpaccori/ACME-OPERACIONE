import pytest

from config import settings
from services_courier_quote import calculate_payment_processing_fee, calculate_service_fee


def test_tarifa_por_tramos():
    assert calculate_service_fee(50) == 6.0
    assert calculate_service_fee(100) == 12.0
    assert calculate_service_fee(150) == 16.0


def test_comision_tarjeta_izipay_y_yape_culqi(monkeypatch):
    monkeypatch.setattr(settings, "payment_provider", "izipay")
    monkeypatch.setattr(settings, "culqi_public_key", "pk_live_x")
    monkeypatch.setattr(settings, "culqi_private_key", "sk_live_x")

    card = calculate_payment_processing_fee(19.5, "card")
    assert card["provider"] == "izipay"
    assert card["fee"] == 0.83

    # Yape por Culqi: en compras chicas manda el minimo por pago.
    yape = calculate_payment_processing_fee(19.5, "yape")
    assert yape["provider"] == "culqi"
    assert yape["fee"] == 3.5
    # En compras grandes manda el porcentaje.
    assert calculate_payment_processing_fee(173, "yape")["fee"] == calculate_payment_processing_fee(173, "card")["fee"]


def test_yape_sin_culqi_no_se_cotiza(monkeypatch):
    monkeypatch.setattr(settings, "payment_provider", "izipay")
    monkeypatch.setattr(settings, "culqi_private_key", "")
    with pytest.raises(ValueError):
        calculate_payment_processing_fee(20, "yape")
