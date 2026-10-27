import json
from pathlib import Path

from services_courier_tariffs import calculate_courier_tariff, sector_fee_for_point

CONFIG = json.loads(Path(__file__).resolve().parents[1].joinpath("courier_tariff_config.json").read_text(encoding="utf-8"))
PLAZA = (-12.7866, -74.9757)
SAN_GERONIMO_ALTO = (-12.7838717, -74.9972883)
HUAYLACUCHO = (-12.7906583, -74.9472017)


def _quote(dest, origin=PLAZA, **kw):
    return calculate_courier_tariff(
        distance_km=2.0,
        destination_lat=dest[0], destination_lng=dest[1],
        origin_lat=origin[0], origin_lng=origin[1],
        **kw,
    )


def test_cada_punto_del_dueno_cobra_su_precio():
    for point in CONFIG["sector_pricing"]["points"]:
        result = sector_fee_for_point(point["lat"], point["lng"])
        assert result["fee"] == point["fee"], point


def test_centro_cobra_7():
    quote = _quote(PLAZA)
    assert quote["tarifa_final"] == 7.0
    assert quote["zona_codigo"] == "S"


def test_sectores_del_dueno():
    assert _quote(SAN_GERONIMO_ALTO)["tarifa_final"] == 12.0
    assert _quote(HUAYLACUCHO)["tarifa_final"] == 10.0


def test_local_fuera_del_centro_cobra_el_mayor():
    quote = _quote(PLAZA, origin=SAN_GERONIMO_ALTO)
    assert quote["tarifa_final"] == 12.0


def test_fuera_de_cobertura_sigue_por_kilometro():
    quote = calculate_courier_tariff(
        distance_km=6.0, is_out_of_city=True,
        destination_lat=-12.85, destination_lng=-74.97,
    )
    assert quote["zona_codigo"] == "D"
    assert quote["tarifa_final"] == 22.0


def test_recargos_se_suman_al_precio_del_sector():
    quote = _quote(PLAZA, service_type="express")
    assert quote["tarifa_final"] == 9.0  # 7 + 30% = 9.10 -> redondeo a 0.50


def test_sin_coordenadas_usa_zonas_por_distancia():
    quote = calculate_courier_tariff(distance_km=1.5)
    assert quote["zona_codigo"] == "A"
