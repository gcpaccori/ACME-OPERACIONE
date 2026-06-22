from concurrent.futures import ThreadPoolExecutor
import logging
import time

from fastapi import APIRouter, Header, HTTPException

from models import (
    CourierPaymentChargeRequest,
    CourierPaymentChargeResponse,
    CourierPaymentOrderRequest,
    CourierPaymentOrderResponse,
)
from services_courier_payments import SupabaseCourierError, SupabaseCourierPayments
from services_culqi import CulqiService


router = APIRouter(prefix="/api/courier/payments", tags=["courier-payments"])

logger = logging.getLogger(__name__)
culqi_service = CulqiService()
CULQI_MIN_ORDER_AMOUNT = 600
CULQI_MAX_ORDER_AMOUNT = 700000


def bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token.strip()


def order_amount_centimos(order: dict) -> int:
    total = float(order.get("total") or 0)
    return int(round(total * 100))


def customer_identity(payload_email: str | None, payload_name: str | None, profile: dict | None) -> tuple[str, str, str | None]:
    email = str(payload_email or (profile or {}).get("email") or "").strip().lower()
    name = " ".join(str(payload_name or (profile or {}).get("full_name") or "Cliente ACME").strip().split())
    phone = " ".join(str((profile or {}).get("phone") or "").strip().split())
    if not email:
        raise HTTPException(status_code=400, detail="El pedido no tiene email de cliente para Culqi.")
    if "@" not in email or "." not in email.rsplit("@", 1)[-1]:
        raise HTTPException(status_code=400, detail="El email del cliente no es valido para Culqi.")
    return email, name or "Cliente ACME", phone or None


def needs_profile_for_order(payload: CourierPaymentOrderRequest) -> bool:
    return not (payload.email_cliente and payload.nombre_cliente and payload.telefono_cliente)


def needs_profile_for_charge(payload: CourierPaymentChargeRequest) -> bool:
    return not (payload.email_cliente and payload.nombre_cliente)


def run_parallel(*jobs) -> None:
    if not jobs:
        return
    with ThreadPoolExecutor(max_workers=len(jobs)) as executor:
        futures = [executor.submit(job) for job in jobs]
        for future in futures:
            future.result()


@router.post("/order", response_model=CourierPaymentOrderResponse)
def create_courier_payment_order(
    payload: CourierPaymentOrderRequest,
    authorization: str | None = Header(default=None),
):
    """
    Crear una orden Culqi para un pedido real del courier en Supabase.
    """
    token = bearer_token(authorization)
    supabase = SupabaseCourierPayments()
    started_at = time.perf_counter()

    try:
        order = supabase.get_order(payload.order_id, token)
        profile = supabase.get_profile(order.get("customer_id"), token) if needs_profile_for_order(payload) else None
        email, name, phone = customer_identity(payload.email_cliente, payload.nombre_cliente, profile)
        amount = order_amount_centimos(order)
        if amount <= 0:
            raise HTTPException(status_code=400, detail="El pedido no tiene un monto valido para cobrar.")
        if amount < CULQI_MIN_ORDER_AMOUNT:
            raise HTTPException(
                status_code=400,
                detail="Culqi requiere un monto minimo de S/ 6.00 para habilitar Yape, PagoEfectivo y billeteras.",
            )
        if amount > CULQI_MAX_ORDER_AMOUNT:
            raise HTTPException(status_code=400, detail="El monto del pedido excede el limite permitido por Culqi.")

        description = payload.descripcion or f"Pedido ACME Courier #{order.get('order_code') or order['id']}"
        result = culqi_service.crear_orden_checkout(
            pedido_id=f"courier-{order['id']}",
            monto=amount,
            email=email,
            nombre=name,
            telefono=payload.telefono_cliente or phone,
            descripcion=description,
        )
        if not result.get("exito"):
            status_code = 400 if result.get("error_tipo") in ("validation", "parameter_error") else 502
            raise HTTPException(status_code=status_code, detail=result.get("mensaje", "No se pudo crear orden Culqi."))

        payment_method_id = supabase.get_online_payment_method_id(token)
        payment_id = supabase.upsert_pending_payment(
            order,
            payment_method_id=payment_method_id,
            external_reference=result["order_id"],
            bearer_token=token,
        )
        run_parallel(
            lambda: supabase.update_order_payment(
                str(order["id"]),
                payment_method_id=payment_method_id,
                payment_status="pending",
                bearer_token=token,
            ),
            lambda: supabase.insert_transaction(
                payment_id=payment_id,
                transaction_type="authorization",
                amount=float(order.get("total") or 0),
                status="pending",
                provider_transaction_id=result["order_id"],
                request_json={"order_id": str(order["id"]), "amount": amount, "currency": "PEN"},
                response_json=result.get("respuesta_completa"),
                bearer_token=token,
            ),
        )
        logger.info("courier_payment_order_ok order_id=%s elapsed_ms=%s", order["id"], int((time.perf_counter() - started_at) * 1000))

        return CourierPaymentOrderResponse(
            order_id=result["order_id"],
            courier_order_id=str(order["id"]),
            payment_id=payment_id,
            monto_centimos=result.get("monto_centimos", amount),
            mensaje=result.get("mensaje", "Orden Culqi creada"),
        )
    except HTTPException:
        raise
    except SupabaseCourierError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/charge", response_model=CourierPaymentChargeResponse)
def charge_courier_payment(
    payload: CourierPaymentChargeRequest,
    authorization: str | None = Header(default=None),
):
    """
    Cobrar un pedido real del courier con un token generado por Culqi Checkout.
    Idempotente: si ya existe un payment_attempt 'paid' para este order_id, devuelve el resultado anterior.
    """
    token = bearer_token(authorization)
    supabase = SupabaseCourierPayments()
    started_at = time.perf_counter()

    try:
        order = supabase.get_order(payload.order_id, token)
        profile = supabase.get_profile(order.get("customer_id"), token) if needs_profile_for_charge(payload) else None
        email, name, _phone = customer_identity(payload.email_cliente, payload.nombre_cliente, profile)
        amount = order_amount_centimos(order)
        if amount <= 0:
            raise HTTPException(status_code=400, detail="El pedido no tiene un monto valido para cobrar.")
        if amount < CULQI_MIN_ORDER_AMOUNT:
            raise HTTPException(status_code=400, detail="Culqi requiere un monto minimo de S/ 6.00 para cobrar este pedido.")
        if amount > CULQI_MAX_ORDER_AMOUNT:
            raise HTTPException(status_code=400, detail="El monto del pedido excede el limite permitido por Culqi.")

        payment_method_id = order.get("payment_method_id") or supabase.get_online_payment_method_id(token)
        payment_id = payload.payment_id
        if not payment_id:
            payment_id = supabase.upsert_pending_payment(
                order,
                payment_method_id=payment_method_id,
                external_reference=f"charge-pending-{order['id']}",
                bearer_token=token,
            )

        # Idempotencia: verificar si ya existe un payment_attempt 'paid' para este order
        idempotency_key = f"charge-{order['id']}-{payment_id}"
        existing_attempt = supabase.get_paid_payment_attempt(str(order["id"]), bearer_token=token)
        if existing_attempt:
            logger.info(
                "courier_payment_charge_idempotent order_id=%s attempt_id=%s",
                order["id"], existing_attempt.get("id"),
            )
            prev_meta = existing_attempt.get("metadata") or {}
            return CourierPaymentChargeResponse(
                exito=True,
                courier_order_id=str(order["id"]),
                payment_id=str(payment_id),
                transaccion_id=str(prev_meta.get("provider_payment_id") or existing_attempt.get("provider_payment_id") or ""),
                mensaje="Pago ya procesado anteriormente (idempotente)",
            )

        # Crear payment_attempt antes del cargo
        supabase.create_payment_attempt(
            order_id=str(order["id"]),
            idempotency_key=idempotency_key,
            amount=float(order.get("total") or 0),
            status="pending",
            bearer_token=token,
        )

        result = culqi_service.procesar_pago(
            token=payload.token,
            monto=amount,
            email=email,
            descripcion=f"Pago ACME Courier #{order.get('order_code') or order['id']} - {name}",
            referencia=f"courier-{order['id']}",
        )

        payment_status = "paid" if result.get("exito") else "failed"
        transaction_id = result.get("transaccion_id")

        # Actualizar payment_attempt con resultado
        supabase.update_payment_attempt(
            idempotency_key=idempotency_key,
            provider_payment_id=transaction_id,
            status=payment_status,
            metadata=result.get("respuesta_completa"),
            bearer_token=token,
        )

        run_parallel(
            lambda: supabase.update_payment_after_charge(
                str(payment_id),
                status=payment_status,
                external_reference=transaction_id or result.get("referencia"),
                bearer_token=token,
            ),
            lambda: supabase.insert_transaction(
                payment_id=str(payment_id),
                transaction_type="capture",
                amount=float(order.get("total") or 0),
                status=payment_status,
                provider_transaction_id=transaction_id,
                request_json={
                    "order_id": str(order["id"]),
                    "amount": amount,
                    "currency": "PEN",
                    "token_prefix": payload.token[:12],
                },
                response_json=result.get("respuesta_completa"),
                bearer_token=token,
            ),
            lambda: supabase.update_order_payment(
                str(order["id"]),
                payment_method_id=payment_method_id,
                payment_status=payment_status,
                bearer_token=token,
            ),
        )
        logger.info("courier_payment_charge_ok order_id=%s status=%s elapsed_ms=%s", order["id"], payment_status, int((time.perf_counter() - started_at) * 1000))

        return CourierPaymentChargeResponse(
            exito=bool(result.get("exito")),
            courier_order_id=str(order["id"]),
            payment_id=str(payment_id),
            transaccion_id=transaction_id,
            mensaje=result.get("mensaje", "Pago procesado"),
        )
    except HTTPException:
        raise
    except SupabaseCourierError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
