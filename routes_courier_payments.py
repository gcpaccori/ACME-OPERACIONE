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


def orden_culqi_reutilizable(consulta: dict, monto_centimos: int) -> bool:
    """
    Decide si una orden Culqi anterior sigue sirviendo para cobrar este pedido.

    Solo se reutiliza si sigue sin pagar ("created"), es por el mismo monto y
    aun no vence. Ante cualquier duda se devuelve False y se crea una nueva:
    equivocarse hacia crear de mas solo deja una orden huerfana, mientras que
    reutilizar una que no corresponde cobraria un monto que no es.
    """
    if not consulta.get("exito"):
        return False
    if str(consulta.get("estado") or "").strip().lower() != "created":
        return False
    try:
        if int(consulta.get("monto_centimos") or 0) != int(monto_centimos):
            return False
    except (TypeError, ValueError):
        return False

    expira = consulta.get("expiration_date")
    if expira is not None:
        try:
            # Margen de 2 minutos: una orden a punto de vencer no le sirve a
            # nadie que este por abrir el checkout.
            if int(expira) <= int(time.time()) + 120:
                return False
        except (TypeError, ValueError):
            return False
    return True


def run_parallel(*jobs) -> None:
    if not jobs:
        return
    with ThreadPoolExecutor(max_workers=len(jobs)) as executor:
        futures = [executor.submit(job) for job in jobs]
        for future in futures:
            future.result()


def _release_paid_order(supabase: SupabaseCourierPayments, order_id: str, token: str | None) -> None:
    """Libera el pedido a operaciones; el cobro ya se hizo, asi que un fallo aqui no lo tumba."""
    try:
        supabase.release_paid_order(order_id, bearer_token=token)
    except Exception as exc:
        logger.error("courier_order_release_failed order_id=%s: %s", order_id, exc, exc_info=True)


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

        # Este endpoint se llama cada vez que el cliente pulsa "Pagar", y antes
        # creaba una orden Culqi nueva en cada llamada pisando la anterior. Con
        # tarjeta daba igual, pero en el flujo asincrono no: quedaban dos
        # ordenes vivas y pagables sobre el mismo pedido, y si el cliente pagaba
        # las dos por Yape se le cobraba dos veces. Por eso reutilizamos la
        # orden anterior mientras siga sirviendo.
        existing = supabase.find_pending_payment(str(order["id"]), token)
        existing_reference = str((existing or {}).get("external_reference") or "").strip()
        reused = False

        if existing_reference:
            consulta = culqi_service.obtener_orden(existing_reference)
            if orden_culqi_reutilizable(consulta, amount):
                reused = True
                result = {
                    "exito": True,
                    "order_id": existing_reference,
                    "monto_centimos": consulta.get("monto_centimos", amount),
                    "mensaje": "Orden Culqi vigente reutilizada",
                }
                logger.info(
                    "courier_payment_order_reused order_id=%s culqi_order=%s",
                    order["id"],
                    existing_reference,
                )

        if not reused:
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
        jobs = [
            lambda: supabase.update_order_payment(
                str(order["id"]),
                payment_method_id=payment_method_id,
                payment_status="pending",
                bearer_token=token,
            )
        ]
        # La transaccion de autorizacion se registra solo cuando la orden es
        # nueva. Al reutilizar no hay nada nuevo que registrar, y anotarlo otra
        # vez llenaria el historial de un intento por cada clic.
        if not reused:
            jobs.append(
                lambda: supabase.insert_transaction(
                    payment_id=payment_id,
                    transaction_type="authorization",
                    amount=float(order.get("total") or 0),
                    status="pending",
                    provider_transaction_id=result["order_id"],
                    request_json={"order_id": str(order["id"]), "amount": amount, "currency": "PEN"},
                    response_json=result.get("respuesta_completa"),
                    bearer_token=token,
                )
            )
        run_parallel(*jobs)
        logger.info(
            "courier_payment_order_ok order_id=%s reused=%s elapsed_ms=%s",
            order["id"],
            reused,
            int((time.perf_counter() - started_at) * 1000),
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
            _release_paid_order(supabase, str(order["id"]), token)
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
        if payment_status == "paid":
            _release_paid_order(supabase, str(order["id"]), token)
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
