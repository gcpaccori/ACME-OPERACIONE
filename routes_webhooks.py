from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import json
from urllib.parse import parse_qs
from uuid import uuid4

import requests
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from config import settings
from services_courier_payments import SupabaseCourierPayments
from services_culqi import CulqiService
from services_izipay import (
    IzipayService,
    courier_order_id_from_izipay,
    izipay_order_id,
    summarize_answer,
)
from services_izipay_sdk import IzipaySdkService, izipay_order_number, summarize_payload

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


def _sb_post(table: str, data: dict[str, Any]) -> None:
    try:
        requests.post(
            f"{_base_url()}/{table}",
            headers={**_supabase_headers(), "Prefer": "return=minimal"},
            json=data,
            timeout=10,
        )
    except Exception as exc:
        logger.warning("webhook _sb_post %s failed: %s", table, exc)


def _get_payment_for_order(order_id: str, provider: str = "culqi") -> dict[str, Any] | None:
    rows = _sb_get(
        "payments",
        {
            "select": "id,status,external_reference",
            "order_id": f"eq.{order_id}",
            "provider": f"eq.{provider}",
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
    provider: str = "culqi",
) -> str | None:
    """
    Aplica al pedido un estado ya confirmado por la pasarela (Culqi o Izipay).
    Lo usan los webhooks y la sincronizacion que piden la web y la app.
    Devuelve el estado aplicado, o None si no correspondia aplicar nada.
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
            {"select": "id,total,payment_status,order_group_id", "id": f"eq.{order_id}", "limit": "1"},
        )
        if not order_rows:
            logger.warning("culqi_webhook: pedido %s no existe", order_id)
            return None
        order = order_rows[0]
        if order.get("order_group_id"):
            # Pedido de varios locales: el pago cubre el grupo entero.
            group_rows = _sb_get("order_groups", {"select": "total", "id": f"eq.{order['order_group_id']}", "limit": "1"})
            if group_rows:
                order["total"] = group_rows[0].get("total")
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

        payment = _get_payment_for_order(order_id, provider)
        if payment:
            payment_update: dict[str, Any] = {"status": new_status, "updated_at": now}
            ts_field = _STATUS_TIMESTAMP_MAP.get(new_status)
            if ts_field:
                payment_update[ts_field] = now
            if new_status == "paid":
                payment_update["authorized_at"] = now
            _sb_patch("payments", {"id": f"eq.{payment.get('id')}"}, payment_update)
            if verified.get("transaction_uuid"):
                # Izipay: se guarda la transaccion que cobro, para auditoria.
                _sb_post(
                    "payment_transactions",
                    {
                        "id": str(uuid4()),
                        "payment_id": payment.get("id"),
                        "transaction_type": "capture",
                        "amount": float(order.get("total") or 0),
                        "provider_transaction_id": verified.get("transaction_uuid"),
                        "status": new_status,
                        "request_json": {"order_id": order_id, "provider": provider},
                        "response_json": verified.get("raw"),
                        "created_at": now,
                    },
                )

        if new_status == "paid":
            # Pago confirmado (Yape, PagoEfectivo...): recien ahora el pedido entra a operaciones.
            SupabaseCourierPayments().release_paid_order(order_id)

        logger.info(
            "payment_status_applied provider=%s type=%s order_id=%s new_status=%s",
            provider, event_type, order_id, new_status,
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


# ---------------------------------------------------------------------------
# Izipay
# ---------------------------------------------------------------------------

def izipay_test_payment_blocked(resumen: dict[str, Any]) -> bool:
    """Un pago de TEST no confirma pedidos reales salvo IZIPAY_ACCEPT_TEST_PAYMENTS."""
    return str(resumen.get("mode") or "").upper() == "TEST" and not settings.izipay_accept_test_payments


def izipay_verified(resumen: dict[str, Any], raw: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """
    Convierte el resumen de una respuesta de Izipay ya autenticada (firma
    valida o consulta directa a Order/Get) al formato que aplica
    apply_verified_status. Devuelve None si no es una orden de un pedido ACME o
    no esta en soles.
    """
    order_id = courier_order_id_from_izipay(resumen.get("order_id"))
    if not order_id:
        return None
    if resumen.get("currency") and resumen.get("currency") != "PEN":
        logger.error("izipay: moneda inesperada %s en %s", resumen.get("currency"), resumen.get("order_id"))
        return None
    if izipay_test_payment_blocked(resumen):
        logger.warning("izipay: pago en modo TEST ignorado para %s", resumen.get("order_id"))
        return None
    return {
        "id": resumen.get("order_id"),
        "amount": int(resumen.get("amount") or 0),
        "metadata": {"pedido_id": order_id},
        "transaction_uuid": resumen.get("transaction_uuid") if resumen.get("status") == "paid" else None,
        "raw": raw,
    }


def apply_izipay_answer(answer: dict[str, Any], event_type: str) -> str | None:
    """Aplica una respuesta de Izipay cuya firma ya se verifico."""
    resumen = summarize_answer(answer)
    new_status = resumen.get("status")
    verified = izipay_verified(resumen, answer)
    if not new_status or not verified:
        return None
    return apply_verified_status(event_type, new_status, verified, provider="izipay")


def sync_izipay_order(courier_order_id: str) -> str | None:
    """
    Consulta en Izipay los intentos de pago del pedido y aplica el resultado.
    Respaldo de la IPN: la web y la app lo piden mientras el pedido espera
    pago. Lanza IzipayUnavailable si Izipay no responde.
    """
    consulta = IzipayService().obtener_orden(izipay_order_id(courier_order_id))
    if not consulta.get("encontrada"):
        return None
    resumen = consulta["resumen"]
    new_status = resumen.get("status")
    verified = izipay_verified(resumen, consulta.get("respuesta_completa"))
    if not new_status or not verified:
        return None
    return apply_verified_status("izipay.order.get", new_status, verified, provider="izipay")


async def _izipay_fields(request: Request) -> dict[str, str]:
    """La IPN llega como formulario (x-www-form-urlencoded); se acepta JSON por si acaso."""
    raw = (await request.body()).decode("utf-8", errors="replace")
    content_type = request.headers.get("content-type", "")
    if "json" in content_type:
        try:
            data = json.loads(raw)
            return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
        except ValueError:
            return {}
    return {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items() if v}


@router.post("/izipay")
async def izipay_ipn(request: Request):
    """
    IPN de Izipay ("URL de notificacion al final del pago").

    Solo se acepta si la firma kr-hash coincide (HMAC-SHA-256 con la contrasena
    de la API REST), y un "pagado" solo si el monto coincide con el total del
    pedido. Responde 200 cuando la notificacion se proceso o no aplica, para
    que Izipay no reintente en vano.
    """
    fields = await _izipay_fields(request)
    kr_answer = fields.get("kr-answer", "")
    if not IzipayService().verificar_firma(kr_answer, fields.get("kr-hash", ""), fields.get("kr-hash-key") or "password"):
        logger.warning("izipay_ipn: firma invalida")
        return PlainTextResponse("Invalid signature", status_code=400)

    answer = IzipayService.leer_respuesta(kr_answer)
    applied = apply_izipay_answer(answer, f"izipay.ipn.{fields.get('kr-answer-type') or 'payment'}")
    logger.info(
        "izipay_ipn_received order=%s status=%s applied=%s",
        (answer.get("orderDetails") or {}).get("orderId"), answer.get("orderStatus"), applied,
    )
    return PlainTextResponse(f"OK {answer.get('orderStatus') or ''}".strip())


# ---------------------------------------------------------------------------
# SDK de Izipay (tarjeta, Yape, Plin, QR)
# ---------------------------------------------------------------------------

def izipay_sdk_verified(resumen: dict[str, Any], raw: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """
    Convierte el resumen de una respuesta del SDK ya autenticada (firma valida
    o consulta directa a Izipay) al formato que aplica apply_verified_status.
    El pedido sale del campo personalizado firmado, y si falta, del numero de
    pedido guardado en payments.
    """
    order_number = resumen.get("order_number")
    if not order_number:
        return None
    courier_order_id = resumen.get("courier_order_id")
    if courier_order_id and izipay_order_number(courier_order_id) != order_number:
        logger.error("izipay_sdk: el pedido %s no corresponde al numero %s", courier_order_id, order_number)
        return None
    if resumen.get("currency") and resumen.get("currency") != "PEN":
        logger.error("izipay_sdk: moneda inesperada %s en %s", resumen.get("currency"), order_number)
        return None
    return {
        "id": order_number,
        "amount": int(resumen.get("amount") or 0),
        "metadata": {"pedido_id": courier_order_id} if courier_order_id else {},
        "transaction_uuid": resumen.get("unique_id") if resumen.get("status") == "paid" else None,
        "raw": raw,
    }


def apply_izipay_sdk_payload(payload: dict[str, Any], event_type: str) -> str | None:
    """Aplica una respuesta del SDK cuya firma ya se verifico."""
    resumen = summarize_payload(payload)
    new_status = resumen.get("status")
    verified = izipay_sdk_verified(resumen, payload)
    if not new_status or not verified:
        return None
    return apply_verified_status(event_type, new_status, verified, provider="izipay_sdk")


def sync_izipay_sdk_order(courier_order_id: str) -> str | None:
    """
    Pregunta a Izipay si el pedido ya se pago y, si es asi, lo aplica.
    Respaldo de la IPN: la web y la app lo piden mientras el pedido espera
    pago. Lanza IzipaySdkUnavailable si Izipay no responde.
    """
    consulta = IzipaySdkService().consultar_orden(izipay_order_number(courier_order_id))
    if not consulta.get("encontrada"):
        return None
    resumen = consulta["resumen"]
    resumen["courier_order_id"] = resumen.get("courier_order_id") or courier_order_id
    verified = izipay_sdk_verified(resumen, consulta.get("respuesta_completa"))
    if not verified:
        return None
    return apply_verified_status("izipay_sdk.order.search", "paid", verified, provider="izipay_sdk")


@router.post("/izipay-sdk")
async def izipay_sdk_ipn(request: Request):
    """
    IPN del SDK de Izipay (campo urlIPN del checkout).

    Llega como JSON con payloadHttp y signature. Solo se acepta si la firma
    coincide (HMAC-SHA-256 con la clave hash), y un "pagado" solo si el monto
    coincide con el total del pedido.
    """
    try:
        body = json.loads((await request.body()).decode("utf-8", errors="replace") or "{}")
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    payload = IzipaySdkService().leer_firmado(str(body.get("payloadHttp") or ""), str(body.get("signature") or ""))
    if payload is None:
        logger.warning("izipay_sdk_ipn: firma invalida (code=%s)", body.get("code"))
        return JSONResponse(status_code=400, content={"received": False})

    applied = apply_izipay_sdk_payload(payload, "izipay_sdk.ipn")
    resumen = summarize_payload(payload)
    logger.info(
        "izipay_sdk_ipn_received order=%s code=%s applied=%s",
        resumen.get("order_number"), resumen.get("code"), applied,
    )
    return {"received": True}
