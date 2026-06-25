from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


CONFIG_PATH = Path(__file__).with_name("courier_tariff_config.json")


@dataclass
class TariffZoneOverride:
    zone_id: str | None
    code: str
    label: str
    base_fee: float | None
    estimated_minutes: int | None = None


def _number(value: Any, fallback: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return fallback
        return float(value)
    except (TypeError, ValueError):
        return fallback


@lru_cache(maxsize=1)
def load_tariff_config() -> dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def extract_zone_code(value: str | None) -> str | None:
    normalized = (value or "").strip().upper()
    if normalized in {"A", "B", "C", "D"}:
        return normalized
    match = re.search(r"\bZONA\s*([ABCD])\b", normalized)
    return match.group(1) if match else None


def round_money(amount: float, increment: float, mode: str) -> float:
    if increment <= 0:
        return round(amount, 2)
    scaled = amount / increment
    if mode == "up":
        rounded = math.ceil(scaled) * increment
    elif mode == "down":
        rounded = math.floor(scaled) * increment
    else:
        rounded = round(scaled) * increment
    return round(rounded + 1e-9, 2)


def _zone_from_config(config: dict[str, Any], code: str) -> dict[str, Any] | None:
    for zone in config.get("zones", []):
        if str(zone.get("code", "")).upper() == code:
            return dict(zone)
    return None


def _detect_zone_code(
    *,
    distance_km: float,
    requested_zone: str | None,
    is_out_of_city: bool,
    config: dict[str, Any],
) -> str:
    manual_code = extract_zone_code(requested_zone)
    if is_out_of_city or manual_code == "D":
        return "D"
    if manual_code in {"A", "B", "C"}:
        return manual_code

    for zone in config.get("zones", []):
        code = str(zone.get("code", "")).upper()
        if code == "D":
            continue
        max_distance = zone.get("max_distance_km")
        if max_distance is not None and distance_km <= _number(max_distance):
            return code
    return "D"


def calculate_courier_tariff(
    *,
    distance_km: float,
    zone: str | None = None,
    weight_kg: float = 1.0,
    service_type: str = "normal",
    is_difficult_zone: bool = False,
    is_out_of_city: bool = False,
    wait_or_second_visit: bool = False,
    fulfillment_type: str = "delivery",
    zone_overrides: dict[str, TariffZoneOverride] | None = None,
) -> dict[str, Any]:
    config = load_tariff_config()
    if fulfillment_type == "pickup":
        return {
            "tarifa_base": 0.0,
            "recargos": [],
            "recargos_total": 0.0,
            "tarifa_final": 0.0,
            "zona_codigo": "PICKUP",
            "zona_aplicada": "Recojo en tienda",
            "zona_id": None,
            "detalle_calculo": "Recojo sin tarifa courier",
        }

    distance = max(0.0, _number(distance_km))
    weight = max(0.0, _number(weight_kg, 1.0))
    service = (service_type or "normal").strip().lower()
    overrides = zone_overrides or {}
    zone_code = _detect_zone_code(
        distance_km=distance,
        requested_zone=zone,
        is_out_of_city=is_out_of_city,
        config=config,
    )
    zone_config = _zone_from_config(config, zone_code) or {}
    override = overrides.get(zone_code)

    if zone_code == "D":
        outside_cfg = config.get("outside_city", {})
        base_fee = _number(outside_cfg.get("base_fee"), _number(zone_config.get("base_fee"), 10.0))
        per_km = _number(outside_cfg.get("cost_per_km"), 2.0)
        tariff_base = base_fee + (distance * per_km)
    else:
        base_fee = override.base_fee if override and override.base_fee is not None else zone_config.get("base_fee")
        tariff_base = _number(base_fee, 0.0)

    zone_label = override.label if override else str(zone_config.get("label") or f"Zona {zone_code}")
    detail = str(zone_config.get("description") or zone_config.get("name") or zone_label)
    surcharges: list[dict[str, Any]] = []

    service_rate = _number((config.get("service_surcharges") or {}).get(service), 0.0)
    if service_rate > 0:
        surcharges.append({
            "code": "service_express" if service == "express" else f"service_{service}",
            "label": f"Servicio {service}",
            "amount": round(tariff_base * service_rate, 2),
        })

    weight_cfg = config.get("weight", {})
    included_kg = _number(weight_cfg.get("included_kg"), 2.0)
    if weight > included_kg:
        step = max(_number(weight_cfg.get("step_kg"), 1.0), 0.01)
        fee_per_step = _number(weight_cfg.get("fee_per_step"), 0.0)
        steps = math.ceil((weight - included_kg) / step)
        surcharges.append({
            "code": "heavy_package",
            "label": "Paquete pesado",
            "amount": round(steps * fee_per_step, 2),
        })

    if is_difficult_zone:
        surcharges.append({
            "code": "difficult_zone",
            "label": "Zona alta o dificil acceso",
            "amount": _number(config.get("difficult_zone_fee"), 3.0),
        })

    if wait_or_second_visit:
        surcharges.append({
            "code": "second_visit",
            "label": "Espera o segunda visita",
            "amount": _number(config.get("wait_or_second_visit_fee"), 3.0),
        })

    surcharges_total = round(sum(_number(item.get("amount")) for item in surcharges), 2)
    raw_total = tariff_base + surcharges_total
    final_total = round_money(
        raw_total,
        _number(config.get("rounding_increment"), 0.5),
        str(config.get("rounding_mode") or "nearest"),
    )

    return {
        "tarifa_base": round(tariff_base, 2),
        "recargos": surcharges,
        "recargos_total": surcharges_total,
        "tarifa_final": final_total,
        "zona_codigo": zone_code,
        "zona_aplicada": zone_label,
        "zona_id": override.zone_id if override else None,
        "detalle_calculo": detail,
    }
