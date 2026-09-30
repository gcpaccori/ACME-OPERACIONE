from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import json

import requests
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from config import settings
from services_courier_payments import SupabaseCourierPayments
from services_culqi import CulqiService

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


# Estados que el webhook puede fijar. Solo se aceptan despues de consultar a
# Culqi: el cuerpo del webhook no viene firmado y cualquiera podria enviar un
# "pagado" falso para que un pedido sin pagar salga a reparto.
_STATUS_TIMESTAMP_MAP = {
    "paid": "captured_at",
    "failed": "failed_at",
    "expired": "failed_at",
}

_ORDER_EVENTS = {"order.status.changed", "order.paid", "order.expired"}
_CHARGE_EVENTS = {
    "charge.paid",
    "payment.paid",
    "charge.creation.succeeded",
    "charge.succeeded",
    "charge.failed",
    "charge.creation.failed",
    "charge.expired",
}


def _parse_event_object(body: dict[str, Any]) -> dict[str, Any]:
    """
    Culqi manda en "data" el objeto (orden o cargo) como texto JSON; algunos
    envios lo mandan ya como objeto o anidado en data.object. Se aceptan los tres.
    """
    data: Any = body.get("data")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            return {}
    if not isinstance(data, dict):
        return {}
    nested = data.get("object")
    if isinstance(nested, dict):
        return nested
    return data


def _order_id_from_metadata(obj: dict[str, Any]) -> str | None:
    metadata = obj.get("metadata") or {}
    if not isinstance(metadata, dict):
        return None
    raw = str(metadata.get("pedido_id") or metadata.get("order_id") or metadata.get("referencia") or "").strip()
    if raw.startswith("courier-"):
        raw = raw[len("courier-"):]
    return raw or None


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
        return str(rows[0].get("order_id") or "") or None
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


def _verified_status(event_type: str, obj: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    """
    Consulta a Culqi el objeto del evento y devuelve (estado, objeto verificado).
    Devuelve (None, None) si no se pudo verificar o no hay nada que aplicar.
    """
    object_id = str(obj.get("id") or "").strip()
    object_kind = str(obj.get("object") or "").strip()
    if not object_id:
        return None, None

    culqi = CulqiService()
    if event_type in _ORDER_EVENTS or object_kind == "order" or object_id.startswith("ord_"):
        consulta = culqi.obtener_orden(object_id)
        if consulta.get("culqi_status") in (400, 401, 404):
            # Culqi no reconoce la orden: evento falso o de otra cuenta.
            return None, None
        if not consulta.get("exito"):
            raise CulqiUnavailable(consulta.get("mensaje") or "No se pudo consultar la orden en Culqi.")
        verified = consulta.get("respuesta_completa") or {}
        state = str(verified.get("state") or "").lower()
        if state == "paid":
            return "paid", verified
        if state == "expired":
            return "expired", verified
        return None, verified

    if event_type in _CHARGE_EVENTS or object_kind == "charge" or object_id.startswith("chr_"):
        consulta = culqi.obtener_transaccion(object_id)
        verified = consulta.get("respuesta_completa") or {}
        if verified.get("object") == "error":
            return None, None
        if not consulta.get("exito") or verified.get("object") != "charge":
            raise CulqiUnavailable(consulta.get("mensaje") or "No se pudo consultar el cargo en Culqi.")
        outcome = verified.get("outcome") or {}
        outcome_type = str(outcome.get("type") or "").lower()
        if outcome_type in ("venta_exitosa", "successful", "success") or verified.get("paid") is True:
            return "paid", verified
        if outcome_type:
            return "failed", verified
        return None, verified

    return None, None


class CulqiUnavailable(Exception):
    pass


@router.post("/culqi")
async def culqi_webhook(request: Request):
    """
    Recibe notificaciones de Culqi (webhooks).

    No confia en el cuerpo: vuelve a consultar la orden o el cargo en Culqi con
    la llave privada y solo aplica el estado que Culqi confirma, y un "pagado"
    solo si el monto coincide con el total del pedido. Devuelve 200 salvo
    cuando Culqi no responde, para que Culqi reintente.
    """
    try:
        body = await request.json()
    except Exception:
        logger.warning("culqi_webhook: cuerpo no es JSON valido")
        return {"received": True}
    if not isinstance(body, dict):
        return {"received": True}

    event_type = str(body.get("type") or "")
    obj = _parse_event_object(body)
    logger.info("culqi_webhook_received type=%s object_id=%s", event_type, obj.get("id"))

    try:
        new_status, verified = _verified_status(event_type, obj)
    except CulqiUnavailable as exc:
        logger.error("culqi_webhook: no se pudo verificar %s: %s", obj.get("id"), exc)
        return JSONResponse(status_code=503, content={"received": False})

    if not new_status or not verified:
        logger.info("culqi_webhook: evento '%s' sin estado que aplicar, ignorando.", event_type)
        return {"received": True}

    apply_verified_status(event_type, new_status, verified, obj)
    return {"received": True}


def apply_verified_status(
    event_type: str,
    new_status: str,
    verified: dict[str, Any],
    obj: dict[str, Any] | None = None,
) -> str | None:
    """
    Aplica al pedido un estado ya confirmado por Culqi. Lo usan el webhook y la
    sincronizacion que piden la web y la app. Devuelve el estado aplicado, o
    None si no correspondia aplicar nada.
    """
    obj = obj or {}
    object_id = str(verified.get("id") or obj.get("id") or "")
    order_id = _order_id_from_metadata(verified) or _find_order_id_from_payment(object_id)
    if not order_id:
        logger.warning("culqi_webhook: no se pudo resolver order_id para %s", object_id)
        return None

    now = datetime.now(timezone.utc).isoformat()

    try:
        order_rows = _sb_get(
            "orders",
            {"select": "id,total,payment_status", "id": f"eq.{order_id}", "limit": "1"},
        )
        if not order_rows:
            logger.warning("culqi_webhook: pedido %s no existe", order_id)
            return None
        order = order_rows[0]
        current_status = order.get("payment_status")

        if new_status == "paid":
            expected = int(round(float(order.get("total") or 0) * 100))
            paid_amount = int(verified.get("amount") or 0)
            if expected <= 0 or paid_amount != expected:
                logger.error(
                    "culqi_webhook: monto no coincide pedido=%s esperado=%s culqi=%s",
                    order_id, expected, paid_amount,
                )
                return None
        elif current_status == "paid":
            # Un cargo fallido o una orden vencida no deshacen un pago ya confirmado.
            return None

        if current_status == new_status:
            if new_status == "paid":
                # Por si el cobro marco el pago pero no alcanzo a liberar el pedido.
                SupabaseCourierPayments().release_paid_order(order_id)
            return new_status

        _sb_patch(
            "orders",
            {"id": f"eq.{order_id}"},
            {"payment_status": new_status, "updated_at": now},
        )

        payment = _get_payment_for_order(order_id)
        if payment:
            payment_update: dict[str, Any] = {"status": new_status, "updated_at": now}
            ts_field = _STATUS_TIMESTAMP_MAP.get(new_status)
            if ts_field:
                payment_update[ts_field] = now
            if new_status == "paid":
                payment_update["authorized_at"] = now
            _sb_patch("payments", {"id": f"eq.{payment.get('id')}"}, payment_update)

        if new_status == "paid":
            # Pago confirmado (Yape, PagoEfectivo...): recien ahora el pedido entra a operaciones.
            SupabaseCourierPayments().release_paid_order(order_id)

        logger.info(
            "culqi_webhook_processed type=%s order_id=%s new_status=%s",
            event_type, order_id, new_status,
        )
        return new_status
    except Exception as exc:
        logger.error("culqi_webhook_error order_id=%s: %s", order_id, exc, exc_info=True)

    return None


def sync_culqi_order(culqi_order_id: str) -> str | None:
    """
    Consulta una orden Culqi (PagoEfectivo, banca movil, agentes, billeteras) y
    aplica su estado. Es el respaldo del webhook: la web y la app lo piden
    mientras el pedido espera pago, asi el pedido se libera aunque el webhook
    no llegue. Lanza CulqiUnavailable si Culqi no responde.
    """
    obj = {"id": culqi_order_id, "object": "order"}
    new_status, verified = _verified_status("order.status.changed", obj)
    if not new_status or not verified:
        return None
    return apply_verified_status("order.status.changed", new_status, verified, obj)
