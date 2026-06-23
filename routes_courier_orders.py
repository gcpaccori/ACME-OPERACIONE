from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException

from models import CourierCreateOrderRequest, CourierCreateOrderResponse
from services_courier_orders import SupabaseOrderError, SupabaseOrderService
from services_supabase_auth import SupabaseAuthError, verify_supabase_user

router = APIRouter(prefix="/api/courier", tags=["courier-orders"])
logger = logging.getLogger(__name__)


def _extract_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token.strip()


@router.post("/orders", response_model=CourierCreateOrderResponse)
def create_courier_order(
    payload: CourierCreateOrderRequest,
    authorization: str | None = Header(default=None),
):
    """
    Crear un pedido real a partir de una cotizacion validada.
    Valida que la cotizacion pertenezca al usuario, no haya expirado y su estado sea 'active'.
    """
    token = _extract_bearer(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Se requiere Bearer token para crear pedidos.")

    try:
        customer_id = verify_supabase_user(token)
    except SupabaseAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    supabase = SupabaseOrderService()

    try:
        # Validar que la cotizacion exista, no haya expirado y pertenezca al usuario
        quote = supabase.get_active_quote(payload.quote_id, customer_id, token)
        if not quote:
            raise HTTPException(
                status_code=422,
                detail="La cotizacion no existe, ha expirado o no pertenece al usuario.",
            )

        # Construir datos de entrega
        addr_data: dict | None = None
        if payload.address:
            addr_data = {
                "line1": payload.address.line1,
                "line2": payload.address.line2,
                "reference": payload.address.reference,
                "district": payload.address.district,
                "city": payload.address.city,
                "region": payload.address.region,
                "country": payload.address.country,
                "lat": payload.address.lat,
                "lng": payload.address.lng,
            }

        delivery_data = {
            "fulfillment_type": payload.fulfillment_type,
            "special_instructions": payload.special_instructions,
            "recipient_name": payload.recipient_name,
            "recipient_phone": payload.recipient_phone,
            "address": addr_data,
        }

        # Crear el pedido
        result = supabase.create_order_from_quote(
            quote=quote,
            delivery_data=delivery_data,
            customer_id=customer_id,
            bearer_token=token,
        )

        logger.info(
            "courier_order_created customer=%s order_id=%s order_code=%s total=%.2f",
            customer_id, result["order_id"], result["order_code"], result["total"],
        )

        return CourierCreateOrderResponse(
            order_id=result["order_id"],
            order_code=result["order_code"],
            total=result["total"],
            payment_status="pending",
        )

    except HTTPException:
        raise
    except SupabaseOrderError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("courier_order_unexpected_error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error interno al crear el pedido: {exc}") from exc
