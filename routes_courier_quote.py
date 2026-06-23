from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException

from models import CourierQuoteRequest, CourierQuoteResponse
from services_courier_quote import (
    SupabaseQuoteError,
    SupabaseQuoteService,
    calculate_delivery_fee,
    calculate_distance_km,
    calculate_service_fee,
)
from services_supabase_auth import SupabaseAuthError, verify_supabase_user

router = APIRouter(prefix="/api/courier", tags=["courier-quote"])
logger = logging.getLogger(__name__)

_FALLBACK_DISTANCE_KM = 1.0  # Distancia minima por defecto cuando no hay coords


def _extract_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token.strip()


@router.post("/quote", response_model=CourierQuoteResponse)
def create_courier_quote(
    payload: CourierQuoteRequest,
    authorization: str | None = Header(default=None),
):
    """
    Generar una cotizacion de precio para un pedido courier.
    Calcula subtotal real desde Supabase, tarifa de servicio, delivery fee y total.
    Guarda la cotizacion en order_quotes con expiracion de 5 minutos.
    """
    token = _extract_bearer(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Se requiere Bearer token para cotizar.")

    try:
        customer_id = verify_supabase_user(token)
    except SupabaseAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    supabase = SupabaseQuoteService()

    try:
        # 1. Obtener datos de la sucursal
        branch = supabase.get_branch(payload.branch_id, token)
        branch_lat = branch.get("lat")
        branch_lng = branch.get("lng")

        # 2. Calcular distancia
        client_lat = payload.latitude
        client_lng = payload.longitude
        has_client_coords = (
            client_lat is not None
            and client_lng is not None
            and not (client_lat == 0.0 and client_lng == 0.0)
        )
        has_branch_coords = (
            branch_lat is not None
            and branch_lng is not None
            and not (branch_lat == 0.0 and branch_lng == 0.0)
        )

        if has_client_coords and has_branch_coords:
            distance_km = calculate_distance_km(
                float(client_lat), float(client_lng),
                float(branch_lat), float(branch_lng),
            )
        else:
            if not has_branch_coords:
                logger.warning(
                    "quote_missing_branch_coords branch_id=%s, usando distancia fallback=%.1f km",
                    payload.branch_id, _FALLBACK_DISTANCE_KM,
                )
            else:
                logger.warning(
                    "quote_missing_client_coords customer_id=%s, usando distancia fallback=%.1f km",
                    customer_id, _FALLBACK_DISTANCE_KM,
                )
            distance_km = _FALLBACK_DISTANCE_KM

        # 3. Calcular delivery_fee (puede lanzar ValueError si fuera de cobertura)
        try:
            delivery_fee = calculate_delivery_fee(distance_km, payload.fulfillment_type)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        # 4. Obtener precios reales de productos desde Supabase
        all_product_ids = list({item.product_id for item in payload.items})
        products_rows = supabase.get_products_with_prices(all_product_ids, token)
        price_map: dict[str, float] = {str(row["id"]): float(row["base_price"]) for row in products_rows}
        name_map: dict[str, str] = {str(row["id"]): str(row.get("name", "")) for row in products_rows}

        # 5. Obtener deltas de modificadores
        all_modifier_ids: list[str] = []
        for item in payload.items:
            all_modifier_ids.extend(item.modifier_ids)
        all_modifier_ids = list(set(all_modifier_ids))

        modifier_map: dict[str, float] = {}
        if all_modifier_ids:
            mod_rows = supabase.get_modifiers_prices(all_modifier_ids, token)
            modifier_map = {str(row["id"]): float(row.get("price_delta", 0)) for row in mod_rows}

        # 6. Calcular subtotal
        subtotal = 0.0
        items_snapshot: list[dict[str, Any]] = []
        for item in payload.items:
            pid = item.product_id
            if pid not in price_map:
                raise HTTPException(
                    status_code=422,
                    detail=f"Producto '{pid}' no encontrado o sin precio en Supabase.",
                )
            unit_price = price_map[pid]
            mod_total = sum(modifier_map.get(mid, 0.0) for mid in item.modifier_ids)
            line_total = (unit_price + mod_total) * item.quantity
            subtotal += line_total
            items_snapshot.append({
                "product_id": pid,
                "product_name": name_map.get(pid, ""),
                "unit_price": unit_price,
                "modifier_ids": item.modifier_ids,
                "modifier_delta": mod_total,
                "quantity": item.quantity,
                "line_total": round(line_total, 2),
            })

        subtotal = round(subtotal, 2)

        # 7. Descuento (por ahora siempre 0)
        discount = 0.0

        # 8. Base para tarifa de servicio
        base = subtotal - discount

        # 9. Tarifa de servicio
        service_fee = calculate_service_fee(base)

        # 10. Tip
        tip_amount = round(float(payload.tip_amount), 2)

        # 11. Total
        total = round(base + service_fee + delivery_fee + tip_amount, 2)

        # 12. Guardar cotizacion
        quote = supabase.save_quote(
            customer_id=customer_id,
            branch_id=payload.branch_id,
            subtotal=subtotal,
            discount=discount,
            service_fee=service_fee,
            service_fee_rate=0.036,
            delivery_fee=delivery_fee,
            tip_amount=tip_amount,
            total=total,
            distance_km=distance_km,
            payment_method=payload.payment_method,
            items_snapshot=items_snapshot,
            bearer_token=token,
        )

        logger.info(
            "courier_quote_ok customer=%s branch=%s subtotal=%.2f total=%.2f dist=%.2fkm",
            customer_id, payload.branch_id, subtotal, total, distance_km,
        )

        return CourierQuoteResponse(
            quote_id=str(quote["id"]),
            subtotal=subtotal,
            discount=discount,
            service_fee=service_fee,
            service_fee_rate=0.036,
            delivery_fee=delivery_fee,
            tip_amount=tip_amount,
            total=total,
            distance_km=distance_km,
            expires_at=str(quote.get("expires_at", "")),
        )

    except HTTPException:
        raise
    except SupabaseQuoteError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("courier_quote_unexpected_error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error interno al generar cotizacion: {exc}") from exc
