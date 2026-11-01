"""Configuracion de cobros del admin de ACME: leer, guardar y simular.

Solo admins de plataforma. Guarda con la clave de servicio, asi que la tabla
public.pricing_config no necesita permisos de escritura para el navegador.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from routes_admin_yape import _es_admin
from services_courier_quote import calculate_road_distance_km
from services_courier_tariffs import calculate_courier_tariff, detect_huancavelica_coverage
from services_pricing import (
    PricingConfigError,
    compute_order_pricing,
    default_config,
    fetch_history,
    fetch_history_entry,
    fetch_stored_row,
    gateway_costs,
    load_pricing_config,
    merge_config,
    save_pricing_config,
    tariff_config,
    validate_config,
)
from services_supabase_auth import SupabaseAuthError, verify_supabase_user

router = APIRouter(prefix="/api/admin/pricing", tags=["admin-pricing"])
logger = logging.getLogger(__name__)

SIMULATED_METHODS = (
    ("yape_directo", "Yape directo"),
    ("card", "Tarjeta"),
    ("yape", "Yape por Culqi"),
)


def _verificar_admin(authorization: str | None) -> str:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="Inicia sesion como administrador.")
    try:
        user_id = verify_supabase_user(token.strip())
    except SupabaseAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    if not _es_admin(user_id):
        raise HTTPException(status_code=403, detail="Solo un administrador de ACME puede ver y cambiar los cobros.")
    return user_id


class SaveConfigRequest(BaseModel):
    config: dict[str, Any]
    # Motivo del cambio, queda en el historial.
    note: Optional[str] = Field(default=None, max_length=500)


class RestoreRequest(BaseModel):
    note: Optional[str] = Field(default=None, max_length=500)


class SimItem(BaseModel):
    price: float = Field(..., ge=0)
    quantity: float = Field(default=1, gt=0)
    merchant_id: Optional[str] = None
    exempt: bool = False


class SimPoint(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)


class SimulateRequest(BaseModel):
    # Borrador sin guardar; si no viene, se simula con lo vigente.
    config: Optional[dict[str, Any]] = None
    items: list[SimItem] = Field(..., min_length=1)
    # Envio a mano, o con origen y destino en el mapa.
    delivery_fee: Optional[float] = Field(default=None, ge=0)
    origin: Optional[SimPoint] = None
    destination: Optional[SimPoint] = None
    branch_count: int = Field(default=1, ge=1, le=20)
    tip: float = Field(default=0, ge=0)
    fulfillment_type: str = "delivery"
    service_type: str = "normal"
    weight_kg: float = Field(default=1.0, ge=0)
    is_difficult_zone: bool = False
    wait_or_second_visit: bool = False


@router.get("/config")
def get_config(authorization: str | None = Header(default=None)):
    _verificar_admin(authorization)
    row = fetch_stored_row(force=True)
    return {
        "config": load_pricing_config(),
        "defaults": default_config(),
        "saved": bool(row),
        "updated_at": (row or {}).get("updated_at"),
        "updated_by": (row or {}).get("updated_by"),
        "version_id": (row or {}).get("version_id"),
        "gateway_costs": gateway_costs(),
        "coverage": tariff_config(default_config()).get("coverage") or {},
    }


@router.put("/config")
def put_config(payload: SaveConfigRequest, authorization: str | None = Header(default=None)):
    user_id = _verificar_admin(authorization)
    try:
        saved = save_pricing_config(payload.config, user_id, payload.note)
    except PricingConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    logger.info("pricing_config_saved by=%s", user_id)
    return saved


@router.get("/history")
def get_history(authorization: str | None = Header(default=None)):
    _verificar_admin(authorization)
    return {"items": fetch_history()}


@router.post("/history/{version_id}/restore")
def restore_version(version_id: int, payload: RestoreRequest | None = None, authorization: str | None = Header(default=None)):
    user_id = _verificar_admin(authorization)
    entry = fetch_history_entry(version_id)
    if not entry or not isinstance(entry.get("config"), dict):
        raise HTTPException(status_code=404, detail=f"No existe la version {version_id}.")
    note = (payload.note if payload else None) or f"Se volvio a la version {version_id}."
    try:
        saved = save_pricing_config(entry["config"], user_id, note)
    except PricingConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    logger.info("pricing_config_restored version=%s by=%s", version_id, user_id)
    return saved


def simulate(payload: SimulateRequest) -> dict[str, Any]:
    if payload.config is not None:
        try:
            cfg = validate_config(merge_config(payload.config))
        except PricingConfigError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    else:
        cfg = load_pricing_config()
    tcfg = tariff_config(cfg)

    delivery: dict[str, Any] = {"source": "manual"}
    if payload.fulfillment_type == "pickup":
        base_delivery = 0.0
        delivery = {"source": "pickup", "zone_label": "Recojo en tienda"}
    elif payload.destination is not None:
        dest = payload.destination
        coverage = detect_huancavelica_coverage(dest.lat, dest.lng, "delivery", config=tcfg)
        if payload.origin is not None:
            distance = calculate_road_distance_km(payload.origin.lat, payload.origin.lng, dest.lat, dest.lng)
        else:
            distance = 1.0
        tariff = calculate_courier_tariff(
            distance_km=distance,
            weight_kg=payload.weight_kg,
            service_type=payload.service_type,
            is_difficult_zone=payload.is_difficult_zone,
            is_out_of_city=bool(coverage.get("is_out_of_city")),
            wait_or_second_visit=payload.wait_or_second_visit,
            destination_lat=dest.lat,
            destination_lng=dest.lng,
            origin_lat=payload.origin.lat if payload.origin else None,
            origin_lng=payload.origin.lng if payload.origin else None,
            config=tcfg,
        )
        base_delivery = float(tariff["tarifa_final"])
        delivery = {
            "source": "map",
            "distance_km": round(distance, 2),
            "coverage": coverage.get("label"),
            "is_out_of_city": bool(coverage.get("is_out_of_city")),
            "zone_label": tariff.get("zona_aplicada"),
            "detail": tariff.get("detalle_calculo"),
            "base": tariff.get("tarifa_base"),
            "surcharges": tariff.get("recargos") or [],
            "fee": base_delivery,
        }
    else:
        base_delivery = float(payload.delivery_fee or 0.0)
        delivery["fee"] = base_delivery

    lines = [
        {"line_total": i.price * i.quantity, "quantity": i.quantity, "merchant_id": i.merchant_id, "exempt": i.exempt}
        for i in payload.items
    ]
    results = []
    for method, label in SIMULATED_METHODS:
        try:
            priced = compute_order_pricing(
                cfg,
                lines=lines,
                delivery_fee=base_delivery,
                branch_count=payload.branch_count,
                tip=payload.tip,
                payment_method=method,
                fulfillment_type=payload.fulfillment_type,
            )
            results.append({"method": method, "label": label, **priced})
        except ValueError as exc:
            results.append({"method": method, "label": label, "error": str(exc)})
    return {"delivery": delivery, "results": results}


@router.post("/simulate")
def post_simulate(payload: SimulateRequest, authorization: str | None = Header(default=None)):
    _verificar_admin(authorization)
    return simulate(payload)
