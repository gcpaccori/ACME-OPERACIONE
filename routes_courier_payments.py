from concurrent.futures import ThreadPoolExecutor
import logging
import time
import uuid

from fastapi import APIRouter, Header, HTTPException

from models import (
    CourierPaymentChargeRequest,
    CourierPaymentChargeResponse,
    CourierPaymentOrderRequest,
    CourierPaymentOrderResponse,
    CourierPaymentSyncRequest,
    CourierPaymentSyncResponse,
    IzipayConfirmRequest,
    IzipayConfirmResponse,
    IzipayDemoConfirmRequest,
    IzipayDemoConfirmResponse,
    IzipayDemoResponse,
    IzipaySdkConfirmRequest,
)
from config import payment_provider_for, settings
from services_courier_payments import SupabaseCourierError, SupabaseCourierPayments
from services_culqi import CulqiService
from services_izipay import (
    IZIPAY_MAX_ORDER_CENTIMOS,
    IzipayService,
    IzipayUnavailable,
    courier_order_id_from_izipay,
    summarize_answer,
)
from services_izipay_sdk import (
    IZIPAY_SDK_MAX_ORDER_CENTIMOS,
    IzipaySdkService,
    IzipaySdkUnavailable,
    izipay_order_number,
    summarize_payload,
)
from services_supabase_auth import SupabaseAuthError, verify_supabase_user
from routes_webhooks import (
    CulqiUnavailable,
    apply_izipay_answer,
    apply_izipay_sdk_payload,
    izipay_test_payment_blocked,
    sync_culqi_order,
    sync_izipay_order,
    sync_izipay_sdk_order,
)


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


def require_order_owner(token: str | None, order: dict) -> None:
    """
    Solo el cliente dueno del pedido puede abrir o cobrar su pago.

    El backend lee Supabase con la service role, que se salta RLS, asi que sin
    esta validacion cualquiera con el id de un pedido podia crearle ordenes
    Culqi o devolver a "pending" un pedido ya pagado.
    """
    if not token:
        raise HTTPException(status_code=401, detail="Inicia sesion para pagar tu pedido.")
    try:
        user_id = verify_supabase_user(token)
    except SupabaseAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    if str(order.get("customer_id") or "") != user_id:
        raise HTTPException(status_code=403, detail="Este pedido no pertenece a tu cuenta.")


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

    if not token:
        raise HTTPException(status_code=401, detail="Inicia sesion para pagar tu pedido.")

    try:
        order = supabase.get_order(payload.order_id, token)
        require_order_owner(token, order)
        if order.get("payment_status") == "paid":
            raise HTTPException(status_code=409, detail="Este pedido ya esta pagado.")
        if order.get("status") == "cancelled":
            raise HTTPException(status_code=409, detail="Este pedido fue cancelado.")
        profile = supabase.get_profile(order.get("customer_id"), token) if needs_profile_for_order(payload) else None
        email, name, phone = customer_identity(payload.email_cliente, payload.nombre_cliente, profile)
        amount = order_amount_centimos(order)
        if amount <= 0:
            raise HTTPException(status_code=400, detail="El pedido no tiene un monto valido para cobrar.")
        try:
            provider = payment_provider_for(payload.payment_method)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if provider == "izipay_sdk":
            return _open_izipay_sdk_payment(
                supabase,
                order,
                token=token,
                email=email,
                name=name,
                phone=payload.telefono_cliente or phone,
                amount=amount,
                started_at=started_at,
            )
        if provider == "izipay":
            return _open_izipay_payment(
                supabase,
                order,
                token=token,
                email=email,
                name=name,
                phone=payload.telefono_cliente or phone,
                amount=amount,
                started_at=started_at,
            )
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


def _open_izipay_payment(
    supabase: SupabaseCourierPayments,
    order: dict,
    *,
    token: str,
    email: str,
    name: str,
    phone: str | None,
    amount: int,
    started_at: float,
) -> CourierPaymentOrderResponse:
    """
    Abre el pago del pedido en Izipay: crea el formToken con el total guardado
    del pedido. El pedido sigue en espera de pago hasta que Izipay confirme
    (respuesta firmada del navegador, IPN o consulta a Order/Get).
    """
    order_id = str(order["id"])
    if amount < settings.izipay_min_order_centimos:
        raise HTTPException(
            status_code=400,
            detail=f"El monto minimo para pagar en linea es S/ {settings.izipay_min_order_centimos / 100:.2f}.",
        )
    if amount > IZIPAY_MAX_ORDER_CENTIMOS:
        raise HTTPException(status_code=400, detail="El monto del pedido excede el limite permitido para pagos en linea.")

    # Si un intento anterior ya se pago (por ejemplo, el navegador se cerro
    # antes de volver), no se abre otro: se aplica el pago y se avisa.
    try:
        if sync_izipay_order(order_id) == "paid":
            raise HTTPException(status_code=409, detail="Este pedido ya esta pagado.")
    except IzipayUnavailable as exc:
        logger.warning("izipay_precheck_unavailable order_id=%s: %s", order_id, exc)

    result = izipay_service().crear_form_token(
        courier_order_id=order_id,
        monto=amount,
        email=email,
        nombre=name,
        telefono=phone,
        referencia_cliente=str(order.get("customer_id") or "") or None,
    )
    if not result.get("exito"):
        status_code = 400 if result.get("error_tipo") in ("validation", "parameter_error") else 502
        raise HTTPException(status_code=status_code, detail=result.get("mensaje", "No se pudo abrir el pago con Izipay."))

    payment_method_id = supabase.get_online_payment_method_id(token)
    existing = supabase.find_pending_payment(order_id, token, provider="izipay")
    payment_id = supabase.upsert_pending_payment(
        order,
        payment_method_id=payment_method_id,
        external_reference=result["order_id"],
        bearer_token=token,
        provider="izipay",
    )
    jobs = [
        lambda: supabase.update_order_payment(
            order_id,
            payment_method_id=payment_method_id,
            payment_status="pending",
            bearer_token=token,
        )
    ]
    # Cada clic en "Pagar" pide un formToken nuevo; el intento se registra una
    # sola vez para no llenar el historial.
    if not existing:
        jobs.append(
            lambda: supabase.insert_transaction(
                payment_id=payment_id,
                transaction_type="authorization",
                amount=float(order.get("total") or 0),
                status="pending",
                provider_transaction_id=result["order_id"],
                request_json={"order_id": order_id, "amount": amount, "currency": "PEN", "provider": "izipay"},
                response_json={"mode": result.get("mode")},
                bearer_token=token,
            )
        )
    run_parallel(*jobs)
    logger.info(
        "courier_payment_izipay_ok order_id=%s mode=%s elapsed_ms=%s",
        order_id,
        result.get("mode"),
        int((time.perf_counter() - started_at) * 1000),
    )
    return CourierPaymentOrderResponse(
        order_id=result["order_id"],
        courier_order_id=order_id,
        payment_id=payment_id,
        monto_centimos=amount,
        mensaje=result.get("mensaje", "Formulario Izipay listo"),
        provider="izipay",
        form_token=result["form_token"],
        public_key=result["public_key"],
        mode=result.get("mode"),
    )


def izipay_service() -> IzipayService:
    return IzipayService()


def izipay_sdk_service() -> IzipaySdkService:
    return IzipaySdkService()


def _open_izipay_sdk_payment(
    supabase: SupabaseCourierPayments,
    order: dict,
    *,
    token: str,
    email: str,
    name: str,
    phone: str | None,
    amount: int,
    started_at: float,
) -> CourierPaymentOrderResponse:
    """
    Abre el pago del pedido con el SDK de Izipay (tarjeta, Yape, Plin, QR): pide
    el token de sesion por el total guardado del pedido y devuelve la
    configuracion del checkout. El pedido sigue en espera de pago hasta que
    Izipay confirme (respuesta firmada del navegador, IPN o consulta de orden).
    """
    order_id = str(order["id"])
    if amount < settings.izipay_min_order_centimos:
        raise HTTPException(
            status_code=400,
            detail=f"El monto minimo para pagar en linea es S/ {settings.izipay_min_order_centimos / 100:.2f}.",
        )
    if amount > IZIPAY_SDK_MAX_ORDER_CENTIMOS:
        raise HTTPException(status_code=400, detail="El monto del pedido excede el limite permitido para pagos en linea.")

    # Si un intento anterior ya se pago (por ejemplo, el navegador se cerro
    # antes de volver), no se abre otro: se aplica el pago y se avisa.
    service = izipay_sdk_service()
    try:
        if sync_izipay_sdk_order(order_id) == "paid":
            raise HTTPException(status_code=409, detail="Este pedido ya esta pagado.")
    except IzipaySdkUnavailable as exc:
        logger.warning("izipay_sdk_precheck_unavailable order_id=%s: %s", order_id, exc)

    result = service.crear_checkout(
        courier_order_id=order_id,
        monto=amount,
        email=email,
        nombre=name,
        telefono=phone,
        cliente_id=str(order.get("customer_id") or "") or None,
    )
    if not result.get("exito"):
        status_code = 400 if result.get("error_tipo") == "validation" else 502
        raise HTTPException(status_code=status_code, detail=result.get("mensaje", "No se pudo abrir el pago con Izipay."))

    payment_method_id = supabase.get_online_payment_method_id(token)
    existing = supabase.find_pending_payment(order_id, token, provider="izipay_sdk")
    payment_id = supabase.upsert_pending_payment(
        order,
        payment_method_id=payment_method_id,
        external_reference=result["order_number"],
        bearer_token=token,
        provider="izipay_sdk",
    )
    jobs = [
        lambda: supabase.update_order_payment(
            order_id,
            payment_method_id=payment_method_id,
            payment_status="pending",
            bearer_token=token,
        )
    ]
    # Cada clic en "Pagar" pide un token nuevo; el intento se registra una
    # sola vez para no llenar el historial.
    if not existing:
        jobs.append(
            lambda: supabase.insert_transaction(
                payment_id=payment_id,
                transaction_type="authorization",
                amount=float(order.get("total") or 0),
                status="pending",
                provider_transaction_id=result["order_number"],
                request_json={"order_id": order_id, "amount": amount, "currency": "PEN", "provider": "izipay_sdk"},
                response_json={"mode": result.get("mode")},
                bearer_token=token,
            )
        )
    run_parallel(*jobs)
    logger.info(
        "courier_payment_izipay_sdk_ok order_id=%s mode=%s elapsed_ms=%s",
        order_id,
        result.get("mode"),
        int((time.perf_counter() - started_at) * 1000),
    )
    return CourierPaymentOrderResponse(
        order_id=result["order_number"],
        courier_order_id=order_id,
        payment_id=payment_id,
        monto_centimos=amount,
        mensaje=result.get("mensaje", "Checkout Izipay listo"),
        provider="izipay_sdk",
        mode=result.get("mode"),
        izipay_checkout={
            "authorization": result["authorization"],
            "key_rsa": result["key_rsa"],
            "script_url": result["script_url"],
            "config": result["config"],
        },
    )


@router.post("/izipay-sdk/confirm", response_model=IzipayConfirmResponse)
def confirm_izipay_sdk_payment(payload: IzipaySdkConfirmRequest):
    """
    Recibe la respuesta que el SDK de Izipay entrega al navegador al terminar
    el pago (payloadHttp y signature) y, si la firma es valida, la aplica al
    pedido. Igual que /izipay/confirm, no pide sesion: la firma prueba que la
    respuesta la genero Izipay.
    """
    signed = izipay_sdk_service().leer_firmado(payload.payload_http, payload.signature)
    if signed is None:
        raise HTTPException(status_code=400, detail="La respuesta de pago no tiene una firma valida.")

    resumen = summarize_payload(signed)
    if resumen.get("order_number") != izipay_order_number(payload.order_id) or (
        resumen.get("courier_order_id") and resumen.get("courier_order_id") != payload.order_id
    ):
        raise HTTPException(status_code=400, detail="La respuesta de pago no corresponde a este pedido.")

    applied = apply_izipay_sdk_payload(signed, "izipay_sdk.browser")
    status = applied or resumen.get("status") or "pending"
    if resumen.get("status") == "paid" and applied != "paid":
        # Firma valida pero el monto no coincide con el pedido, o no se pudo
        # guardar. La IPN y la sincronizacion lo vuelven a intentar.
        logger.error("izipay_sdk_confirm_not_applied order_id=%s resumen=%s", payload.order_id, resumen)
        return IzipayConfirmResponse(
            exito=False,
            courier_order_id=payload.order_id,
            payment_status="pending",
            transaccion_id=resumen.get("unique_id"),
            mensaje="Recibimos tu pago y lo estamos validando.",
        )

    return IzipayConfirmResponse(
        exito=status == "paid",
        courier_order_id=payload.order_id,
        payment_status=status,
        transaccion_id=resumen.get("unique_id"),
        mensaje="Pago confirmado" if status == "paid" else (resumen.get("message") or "El pago no se completo."),
    )


@router.post("/izipay/confirm", response_model=IzipayConfirmResponse)
def confirm_izipay_payment(payload: IzipayConfirmRequest):
    """
    Recibe la respuesta que Izipay entrega al navegador al terminar el pago
    (kr-answer y kr-hash) y, si la firma es valida, la aplica al pedido.

    No pide sesion: la firma HMAC-SHA-256 prueba que la respuesta la genero
    Izipay, y la pagina de pago de la app movil no tiene la sesion del
    cliente. La IPN confirma lo mismo por su lado; esto solo acelera que el
    pedido salga a operaciones.
    """
    service = izipay_service()
    if not service.verificar_firma(payload.kr_answer, payload.kr_hash, payload.kr_hash_key or "sha256_hmac"):
        raise HTTPException(status_code=400, detail="La respuesta de pago no tiene una firma valida.")

    answer = IzipayService.leer_respuesta(payload.kr_answer)
    resumen = summarize_answer(answer)
    courier_order_id = courier_order_id_from_izipay(resumen.get("order_id"))
    if courier_order_id != payload.order_id:
        raise HTTPException(status_code=400, detail="La respuesta de pago no corresponde a este pedido.")

    applied = apply_izipay_answer(answer, "izipay.browser")
    status = applied or resumen.get("status") or "pending"
    if resumen.get("status") == "paid" and izipay_test_payment_blocked(resumen):
        return IzipayConfirmResponse(
            exito=False,
            courier_order_id=payload.order_id,
            payment_status="pending",
            transaccion_id=resumen.get("transaction_uuid"),
            mensaje="Izipay esta en modo de prueba: este pago no confirma el pedido.",
        )
    if resumen.get("status") == "paid" and applied != "paid":
        # Firma valida pero el monto no coincide con el pedido, o no se pudo
        # guardar. La IPN y la sincronizacion lo vuelven a intentar.
        logger.error("izipay_confirm_not_applied order_id=%s resumen=%s", payload.order_id, resumen)
        return IzipayConfirmResponse(
            exito=False,
            courier_order_id=payload.order_id,
            payment_status="pending",
            transaccion_id=resumen.get("transaction_uuid"),
            mensaje="Recibimos tu pago y lo estamos validando.",
        )

    return IzipayConfirmResponse(
        exito=status == "paid",
        courier_order_id=payload.order_id,
        payment_status=status,
        transaccion_id=resumen.get("transaction_uuid"),
        mensaje="Pago confirmado" if status == "paid" else (resumen.get("error_message") or "El pago no se completo."),
    )


IZIPAY_DEMO_ORDER_PREFIX = "demo-"


def _izipay_demo_service() -> IzipayService:
    service = izipay_service()
    if not settings.izipay_demo_enabled or not service.configured:
        raise HTTPException(status_code=404, detail="Not Found")
    return service


@router.post("/izipay/demo", response_model=IzipayDemoResponse)
def create_izipay_demo_payment():
    """
    Formulario de Izipay por un monto fijo, sin pedido ni sesion, para la
    pagina publica /pago-izipay que Izipay revisa al validar el sitio. El
    orderId no lleva el prefijo de pedidos, asi que la IPN no lo aplica a
    nada. Solo responde con IZIPAY_DEMO_ENABLED.
    """
    service = _izipay_demo_service()
    result = service.crear_form_token(
        courier_order_id="demo",
        monto=settings.izipay_demo_amount_centimos,
        email="demo@acmepedidos.com",
        nombre="Cliente Demo",
        telefono=None,
        referencia_cliente=None,
        order_id=f"{IZIPAY_DEMO_ORDER_PREFIX}{uuid.uuid4().hex[:12]}",
    )
    if not result.get("exito"):
        raise HTTPException(status_code=502, detail=result.get("mensaje") or "Izipay no respondio.")
    return IzipayDemoResponse(
        order_id=result["order_id"],
        form_token=result["form_token"],
        public_key=result["public_key"],
        mode=result["mode"],
        monto_centimos=result["monto_centimos"],
    )


@router.post("/izipay/demo/confirm", response_model=IzipayDemoConfirmResponse)
def confirm_izipay_demo_payment(payload: IzipayDemoConfirmRequest):
    """Verifica la firma de la respuesta de la demo y dice si se pago. No guarda nada."""
    service = _izipay_demo_service()
    if not service.verificar_firma(payload.kr_answer, payload.kr_hash, payload.kr_hash_key or "sha256_hmac"):
        raise HTTPException(status_code=400, detail="La respuesta de pago no tiene una firma valida.")
    resumen = summarize_answer(IzipayService.leer_respuesta(payload.kr_answer))
    if not str(resumen.get("order_id") or "").startswith(IZIPAY_DEMO_ORDER_PREFIX):
        raise HTTPException(status_code=400, detail="La respuesta no es de la pagina de demostracion.")
    status = resumen.get("status") or "pending"
    return IzipayDemoConfirmResponse(
        exito=status == "paid",
        payment_status=status,
        mensaje="Pago aprobado" if status == "paid" else (resumen.get("error_message") or "El pago no se completo."),
    )


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

    if not token:
        raise HTTPException(status_code=401, detail="Inicia sesion para pagar tu pedido.")

    try:
        order = supabase.get_order(payload.order_id, token)
        require_order_owner(token, order)
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


@router.post("/sync", response_model=CourierPaymentSyncResponse)
def sync_courier_payment(
    payload: CourierPaymentSyncRequest,
    authorization: str | None = Header(default=None),
):
    """
    Consulta en Culqi el pago diferido de un pedido (PagoEfectivo, banca movil,
    agentes, billeteras) y, si ya se pago, lo marca pagado y lo manda a
    operaciones. La web y la app lo llaman mientras el pedido espera pago, asi
    no depende solo del webhook.
    """
    token = bearer_token(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Inicia sesion para ver tu pedido.")

    supabase = SupabaseCourierPayments()
    try:
        order = supabase.get_order(payload.order_id, token)
        require_order_owner(token, order)
        order_id = str(order["id"])
        status = str(order.get("payment_status") or "pending")

        if status != "paid":
            applied = None
            # Order/Get responde "sin transacciones" a los pedidos que nunca se
            # abrieron en Izipay, asi que se puede consultar siempre.
            if izipay_sdk_service().configured:
                try:
                    applied = sync_izipay_sdk_order(order_id)
                except IzipaySdkUnavailable as exc:
                    logger.warning("courier_payment_sync_izipay_sdk_unavailable order_id=%s: %s", order_id, exc)
            if not applied and izipay_service().configured:
                try:
                    applied = sync_izipay_order(order_id)
                except IzipayUnavailable as exc:
                    logger.warning("courier_payment_sync_izipay_unavailable order_id=%s: %s", order_id, exc)
            if not applied:
                pending = supabase.find_pending_payment(order_id, token)
                reference = str((pending or {}).get("external_reference") or "").strip()
                if reference.startswith("ord_"):
                    try:
                        applied = sync_culqi_order(reference)
                    except CulqiUnavailable as exc:
                        logger.warning("courier_payment_sync_unavailable order_id=%s: %s", order_id, exc)
            if applied:
                status = applied
        else:
            # Por si el pago quedo registrado pero el pedido no alcanzo a salir.
            _release_paid_order(supabase, order_id, token)

        return CourierPaymentSyncResponse(order_id=order_id, payment_status=status)
    except HTTPException:
        raise
    except SupabaseCourierError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
