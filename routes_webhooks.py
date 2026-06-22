from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import requests
from fastapi import APIRouter, Request

from config import settings

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Supabase helper (usa service role key, sin bearer del usuario)
# ---------------------------------------------------------------------------
_BASE_URL: str | None = None
_API_KEY: str | None = None


def _supabase_headers() -> dict[str, str]:
    key = settings.supabase_service_role_key or settings.supabase_key or ""
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _base_url() -> str:
    return f"{settings.supabase_url.rstrip('/')}/rest/v1"


def _sb_patch(table: str, params: dict[str, str], data: dict[str, Any]) -> None:
    """PATCH en Supabase usando service role key."""
    try:
        requests.patch(
            f"{_base_url()}/{table}",
            headers=_supabase_headers(),
            params=params,
            json=data,
            timeout=10,
        )
    except Exception as exc:
        logger.warning("webhook _sb_patch %s failed: %s", table, exc)


def _sb_get(table: str, params: dict[str, str]) -> list[dict[str, Any]]:
    """GET en Supabase usando service role key."""
    try:
        resp = requests.get(
            f"{_base_url()}/{table}",
            headers=_supabase_headers(),
            params=params,
            timeout=10,
        )
        if resp.status_code == 200 and resp.text:
            return resp.json() or []
    except Exception as exc:
        logger.warning("webhook _sb_get %s failed: %s", table, exc)
    return []


# Mapeo de evento Culqi -> payment_status
_EVENT_STATUS_MAP = {
    "charge.paid": "paid",
    "payment.paid": "paid",
    "charge.failed": "failed",
    "charge.expired": "expired",
    "refund.created": "refunded",
    "charge.refunded": "refunded",
}

# Mapeo de payment_status -> campo timestamp en payments
_STATUS_TIMESTAMP_MAP = {
    "paid": "captured_at",
    "failed": "failed_at",
    "expired": "failed_at",
    "refunded": "captured_at",
}


def _find_order_id_from_event(event_object: dict[str, Any]) -> str | None:
    """Intenta extraer order_id del metadata del evento Culqi."""
    metadata = event_object.get("metadata") or {}
    # Culqi puede incluir pedido_id en metadata
    pedido_id = metadata.get("pedido_id") or metadata.get("order_id")
    if pedido_id:
        # Podria ser "courier-<uuid>"
        raw = str(pedido_id)
        if raw.startswith("courier-"):
            raw = raw[len("courier-"):]
        return raw or None
    return None


def _find_order_id_from_payment(external_reference: str | None) -> str | None:
    """Busca en payments por external_reference (id de charge o order Culqi)."""
    if not external_reference:
        return None
    rows = _sb_get(
        "payments",
        {
            "select": "order_id",
            "external_reference": f"eq.{external_reference}",
            "limit": "1",
        },
    )
    if rows:
        return str(rows[0].get("order_id") or "")
    return None


def _get_payment_for_order(order_id: str) -> dict[str, Any] | None:
    rows = _sb_get(
        "payments",
        {
            "select": "id,status,external_reference",
            "order_id": f"eq.{order_id}",
            "provider": "eq.culqi",
            "order": "requested_at.desc",
            "limit": "1",
        },
    )
    return rows[0] if rows else None


@router.post("/culqi")
async def culqi_webhook(request: Request):
    """
    Recibe notificaciones de Culqi (webhooks).
    Actualiza payment_status en orders y payments segun el tipo de evento.
    Siempre devuelve HTTP 200 para que Culqi no reintente.
    """
    try:
        body = await request.json()
    except Exception:
        logger.warning("culqi_webhook: cuerpo no es JSON valido")
        return {"received": True}

    event_type: str = str(body.get("type") or "")
    event_data: dict[str, Any] = body.get("data") or {}
    event_object: dict[str, Any] = event_data.get("object") or {}

    logger.info("culqi_webhook_received type=%s object_id=%s", event_type, event_object.get("id"))

    # Determinar nuevo payment_status
    new_status = _EVENT_STATUS_MAP.get(event_type)
    if not new_status:
        logger.info("culqi_webhook: tipo de evento no manejado '%s', ignorando.", event_type)
        return {"received": True}

    # Resolver order_id
    order_id = _find_order_id_from_event(event_object)
    if not order_id:
        charge_id = event_object.get("id")
        order_id = _find_order_id_from_payment(charge_id)

    if not order_id:
        logger.warning("culqi_webhook: no se pudo resolver order_id para evento '%s'", event_type)
        return {"received": True}

    now = datetime.now(timezone.utc).isoformat()

    try:
        # Idempotencia: leer estado actual de la orden
        order_rows = _sb_get(
            "orders",
            {"select": "id,payment_status", "id": f"eq.{order_id}", "limit": "1"},
        )
        if order_rows:
            current_order_status = order_rows[0].get("payment_status")
            if current_order_status == new_status:
                logger.info(
                    "culqi_webhook: orden %s ya tiene payment_status='%s', idempotente.",
                    order_id, new_status,
                )
                return {"received": True}

        # Actualizar orders.payment_status
        _sb_patch(
            "orders",
            {"id": f"eq.{order_id}"},
            {"payment_status": new_status, "updated_at": now},
        )

        # Actualizar payments
        payment = _get_payment_for_order(order_id)
        if payment:
            payment_id = payment.get("id")
            ts_field = _STATUS_TIMESTAMP_MAP.get(new_status)
            payment_update: dict[str, Any] = {
                "status": new_status,
                "updated_at": now,
            }
            if ts_field:
                payment_update[ts_field] = now
            if new_status == "paid":
                payment_update["authorized_at"] = now
            _sb_patch(
                "payments",
                {"id": f"eq.{payment_id}"},
                payment_update,
            )

        logger.info(
            "culqi_webhook_processed type=%s order_id=%s new_status=%s",
            event_type, order_id, new_status,
        )

    except Exception as exc:
        logger.error("culqi_webhook_error order_id=%s: %s", order_id, exc, exc_info=True)
        # No relanzar: siempre devolver 200 para que Culqi no reintente

    return {"received": True}
