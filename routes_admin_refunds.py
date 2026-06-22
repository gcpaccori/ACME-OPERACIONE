from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import requests
from fastapi import APIRouter, Header, HTTPException

from config import settings
from models import AdminRefundRequest, AdminRefundResponse

router = APIRouter(prefix="/api/admin", tags=["admin-refunds"])
logger = logging.getLogger(__name__)

CULQI_API_BASE = "https://api.culqi.com/v2"


# ---------------------------------------------------------------------------
# Helpers Supabase con service role key
# ---------------------------------------------------------------------------

def _sb_headers() -> dict[str, str]:
    key = settings.supabase_service_role_key or settings.supabase_key or ""
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _base_url() -> str:
    return f"{settings.supabase_url.rstrip('/')}/rest/v1"


def _sb_get(table: str, params: dict[str, str]) -> list[dict[str, Any]]:
    resp = requests.get(
        f"{_base_url()}/{table}",
        headers=_sb_headers(),
        params=params,
        timeout=10,
    )
    if resp.status_code >= 400:
        raise HTTPException(status_code=500, detail=f"Supabase {table}: {resp.text[:300]}")
    return resp.json() or []


def _sb_patch(table: str, params: dict[str, str], data: dict[str, Any]) -> None:
    resp = requests.patch(
        f"{_base_url()}/{table}",
        headers=_sb_headers(),
        params=params,
        json=data,
        timeout=10,
    )
    if resp.status_code >= 400:
        raise HTTPException(status_code=500, detail=f"Supabase PATCH {table}: {resp.text[:300]}")


def _sb_post(table: str, data: dict[str, Any]) -> list[dict[str, Any]] | None:
    headers = {**_sb_headers(), "Prefer": "return=representation"}
    resp = requests.post(
        f"{_base_url()}/{table}",
        headers=headers,
        json=data,
        timeout=10,
    )
    if resp.status_code >= 400:
        raise HTTPException(status_code=500, detail=f"Supabase POST {table}: {resp.text[:300]}")
    return resp.json() if resp.text else None


# ---------------------------------------------------------------------------
# Culqi refund helper
# ---------------------------------------------------------------------------

def _culqi_refund(charge_id: str, amount_centimos: int, reason: str | None) -> dict[str, Any]:
    """Ejecutar un reembolso en Culqi via DELETE /refunds."""
    private_key = settings.culqi_private_key
    headers = {
        "Authorization": f"Bearer {private_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {
        "amount": amount_centimos,
        "charge_id": charge_id,
        "reason": reason or "solicitud_comprador",
    }
    resp = requests.delete(
        f"{CULQI_API_BASE}/refunds",
        headers=headers,
        json=payload,
        timeout=20,
    )
    try:
        data = resp.json()
    except Exception:
        data = {"merchant_message": resp.text}
    return {"status": resp.status_code, "data": data}


# ---------------------------------------------------------------------------
# JWT helper para verificacion basica de rol admin
# ---------------------------------------------------------------------------

def _extract_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token.strip()


def _decode_jwt_sub(token: str) -> str | None:
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        padding = 4 - len(parts[1]) % 4
        padded = parts[1] + ("=" * (padding % 4))
        decoded = base64.urlsafe_b64decode(padded)
        payload = json.loads(decoded)
        return str(payload.get("sub") or "")
    except Exception:
        return None


def _verify_admin(token: str) -> str:
    """Verifica que el usuario tenga rol admin en profiles. Devuelve user_id."""
    user_id = _decode_jwt_sub(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Token invalido.")

    rows = _sb_get(
        "profiles",
        {
            "select": "user_id,default_role",
            "user_id": f"eq.{user_id}",
            "limit": "1",
        },
    )
    if not rows:
        raise HTTPException(status_code=403, detail="Perfil no encontrado.")
    role = str(rows[0].get("default_role") or rows[0].get("role") or "")
    if role not in ("admin", "superadmin", "staff"):
        raise HTTPException(status_code=403, detail="Acceso denegado: se requiere rol admin.")
    return user_id


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/refunds", response_model=AdminRefundResponse)
def admin_refund(
    payload: AdminRefundRequest,
    authorization: str | None = Header(default=None),
):
    """
    Ejecutar un reembolso (total o parcial) sobre un pedido pagado con Culqi.
    Requiere Bearer token de usuario con rol admin.
    """
    token = _extract_bearer(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Se requiere Bearer token de administrador.")

    _verify_admin(token)

    # Obtener el pedido
    order_rows = _sb_get(
        "orders",
        {"select": "id,total,payment_status,customer_id", "id": f"eq.{payload.order_id}", "limit": "1"},
    )
    if not order_rows:
        raise HTTPException(status_code=404, detail="Pedido no encontrado.")
    order = order_rows[0]

    if order.get("payment_status") != "paid":
        raise HTTPException(
            status_code=400,
            detail=f"El pedido no esta pagado (payment_status='{order.get('payment_status')}').",
        )

    # Buscar payment con Culqi y estado 'paid'
    payment_rows = _sb_get(
        "payments",
        {
            "select": "id,amount,currency,external_reference,status",
            "order_id": f"eq.{payload.order_id}",
            "provider": "eq.culqi",
            "status": "eq.paid",
            "limit": "1",
        },
    )
    if not payment_rows:
        raise HTTPException(status_code=400, detail="No se encontro un pago con Culqi en estado 'paid' para este pedido.")

    payment = payment_rows[0]
    charge_id = payment.get("external_reference")
    if not charge_id:
        raise HTTPException(status_code=400, detail="El pago no tiene referencia externa de Culqi.")

    # Determinar monto del reembolso
    order_total = float(order.get("total") or payment.get("amount") or 0)
    refund_amount = float(payload.amount) if payload.amount is not None else order_total
    refund_amount_centimos = int(round(refund_amount * 100))

    if refund_amount_centimos <= 0:
        raise HTTPException(status_code=400, detail="El monto del reembolso debe ser mayor a cero.")

    # Ejecutar reembolso en Culqi
    culqi_result = _culqi_refund(charge_id, refund_amount_centimos, payload.reason)
    culqi_data = culqi_result.get("data", {})
    culqi_status = culqi_result.get("status", 0)

    if culqi_status not in (200, 201) or culqi_data.get("object") == "error":
        error_msg = (
            culqi_data.get("merchant_message")
            or culqi_data.get("user_message")
            or "Error desconocido en Culqi"
        )
        logger.warning(
            "admin_refund_culqi_error order=%s charge=%s status=%s msg=%s",
            payload.order_id, charge_id, culqi_status, error_msg,
        )
        raise HTTPException(status_code=502, detail=f"Culqi rechazo el reembolso: {error_msg}")

    refund_id = culqi_data.get("id")
    now = datetime.now(timezone.utc).isoformat()

    # Actualizar payments
    _sb_patch(
        "payments",
        {"id": f"eq.{payment['id']}"},
        {"status": "refunded", "updated_at": now},
    )

    # Actualizar orders
    _sb_patch(
        "orders",
        {"id": f"eq.{payload.order_id}"},
        {"payment_status": "refunded", "updated_at": now},
    )

    # Registrar en payment_transactions
    try:
        _sb_post(
            "payment_transactions",
            {
                "id": str(uuid4()),
                "payment_id": payment["id"],
                "transaction_type": "refund",
                "amount": refund_amount,
                "provider_transaction_id": refund_id,
                "status": "refunded",
                "request_json": {"charge_id": charge_id, "amount": refund_amount_centimos, "reason": payload.reason},
                "response_json": culqi_data,
                "created_at": now,
            },
        )
    except Exception as exc:
        logger.warning("admin_refund: no se pudo insertar en payment_transactions: %s", exc)

    logger.info(
        "admin_refund_ok order=%s charge=%s refund_id=%s amount=%.2f",
        payload.order_id, charge_id, refund_id, refund_amount,
    )

    return AdminRefundResponse(
        exito=True,
        refund_id=refund_id,
        amount=refund_amount,
        mensaje=f"Reembolso de S/ {refund_amount:.2f} procesado exitosamente.",
    )
