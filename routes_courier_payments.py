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

culqi_service = CulqiService()


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
    email = payload_email or (profile or {}).get("email")
    name = payload_name or (profile or {}).get("full_name") or "Cliente ACME"
    phone = (profile or {}).get("phone")
    if not email:
        raise HTTPException(status_code=400, detail="El pedido no tiene email de cliente para Culqi.")
    return str(email), str(name), str(phone) if phone else None


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

    try:
        order = supabase.get_order(payload.order_id, token)
        profile = supabase.get_profile(order.get("customer_id"), token)
        email, name, phone = customer_identity(payload.email_cliente, payload.nombre_cliente, profile)
        amount = order_amount_centimos(order)
        if amount <= 0:
            raise HTTPException(status_code=400, detail="El pedido no tiene un monto valido para cobrar.")

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
            raise HTTPException(status_code=502, detail=result.get("mensaje", "No se pudo crear orden Culqi."))

        payment_method_id = supabase.get_online_payment_method_id(token)
        payment_id = supabase.upsert_pending_payment(
            order,
            payment_method_id=payment_method_id,
            external_reference=result["order_id"],
            bearer_token=token,
        )
        supabase.update_order_payment(
            str(order["id"]),
            payment_method_id=payment_method_id,
            payment_status="pending",
            bearer_token=token,
        )
        supabase.insert_transaction(
            payment_id=payment_id,
            transaction_type="order",
            amount=float(order.get("total") or 0),
            status="pending",
            provider_transaction_id=result["order_id"],
            request_json={"order_id": str(order["id"]), "amount": amount, "currency": "PEN"},
            response_json=result.get("respuesta_completa"),
            bearer_token=token,
        )

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
    """
    token = bearer_token(authorization)
    supabase = SupabaseCourierPayments()

    try:
        order = supabase.get_order(payload.order_id, token)
        profile = supabase.get_profile(order.get("customer_id"), token)
        email, name, _phone = customer_identity(payload.email_cliente, payload.nombre_cliente, profile)
        amount = order_amount_centimos(order)
        if amount <= 0:
            raise HTTPException(status_code=400, detail="El pedido no tiene un monto valido para cobrar.")

        payment_method_id = order.get("payment_method_id") or supabase.get_online_payment_method_id(token)
        payment_id = payload.payment_id
        if not payment_id:
            payment_id = supabase.upsert_pending_payment(
                order,
                payment_method_id=payment_method_id,
                external_reference=f"charge-pending-{order['id']}",
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
        supabase.update_payment_after_charge(
            str(payment_id),
            status=payment_status,
            external_reference=transaction_id or result.get("referencia"),
            bearer_token=token,
        )
        supabase.insert_transaction(
            payment_id=str(payment_id),
            transaction_type="charge",
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
        )
        supabase.update_order_payment(
            str(order["id"]),
            payment_method_id=payment_method_id,
            payment_status=payment_status,
            bearer_token=token,
        )

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

