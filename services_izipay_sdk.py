"""
Cliente del SDK de Izipay (boton de pagos de Punto Web: tarjeta, Yape, Plin, QR).

Es otro producto que el formulario incrustado de services_izipay.py: usa las
credenciales del panel de comercio (panel.izipay.pe > Desarrolladores > Claves)
y es el unico que cobra con Yape. Flujo (developers.izipay.pe/web-core):

1. El backend pide un token de sesion (security/v1/Token/Generate) con el
   codigo de comercio, la clave API, el numero de pedido y el monto, y arma la
   configuracion del checkout con esos mismos datos.
2. El navegador carga el SDK y muestra el checkout con ese token y la llave
   publica RSA. Al terminar, el SDK entrega la respuesta con payloadHttp y su
   firma.
3. Izipay avisa tambien al backend por la IPN (urlIPN), con el mismo formato.

La firma es HMAC-SHA-256 de payloadHttp con la clave hash, en base64. Solo
payloadHttp esta firmado: el estado del pago se lee de ahi y no de los campos
sueltos de la respuesta.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import time
import unicodedata
import uuid
from typing import Any, Dict

import requests

from config import settings

logger = logging.getLogger(__name__)

IZIPAY_SDK_URLS = {
    "sandbox": {
        "api": "https://sandbox-api-pw.izipay.pe",
        "checkout_js": "https://sandbox-checkout.izipay.pe/payments/v1/js/index.js",
    },
    "production": {
        "api": "https://api-pw.izipay.pe",
        "checkout_js": "https://checkout.izipay.pe/payments/v1/js/index.js",
    },
}
# Yape acepta hasta S/ 2,000 por operacion; para todos los medios se usa ese tope.
IZIPAY_SDK_MAX_ORDER_CENTIMOS = 200000
# El campo personalizado field1 lleva el id del pedido. Vuelve dentro de la
# respuesta firmada, asi se sabe a que pedido pertenece cada pago.
ORDER_FIELD = "field1"
CODE_APPROVED = "00"
# Con estos codigos la respuesta no viene firmada (la doc pide no validarla).
UNSIGNED_CODES = {"021", "COMMUNICATION_ERROR"}

_NAME_RE = re.compile(r"[^a-zA-ZñÑáéíóúÁÉÍÓÚüÜ ]+")


class IzipaySdkUnavailable(Exception):
    """Izipay no respondio o respondio algo que no se puede interpretar."""


def izipay_order_number(courier_order_id: str) -> str:
    """
    Numero de pedido para Izipay (5 a 15 caracteres). Es fijo por pedido, como
    el orderId del formulario incrustado: todos los intentos quedan bajo el
    mismo numero y la consulta de orden los ve juntos.
    """
    compact = str(courier_order_id).replace("-", "").upper()
    return f"AC{compact[:13]}"


def new_transaction_id() -> str:
    """Id de correlacion de Izipay: alfanumerico, unico por token de sesion."""
    return uuid.uuid4().hex[:24]


def centimos_to_amount(centimos: int) -> str:
    return f"{int(centimos) / 100:.2f}"


def amount_to_centimos(amount: Any) -> int:
    try:
        return int(round(float(str(amount).replace(",", "")) * 100))
    except (TypeError, ValueError):
        return 0


def _plain_name(value: str | None, fallback: str) -> str:
    """Izipay solo acepta letras y espacios en nombres y apellidos."""
    text = " ".join(_NAME_RE.sub(" ", str(value or "")).split())
    return text[:50] if len(text) >= 2 else fallback


def _ascii_letters(value: str, fallback: str) -> str:
    """Ciudad y departamento: solo letras sin tildes y espacios."""
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    text = " ".join(re.sub(r"[^A-Za-z ]+", " ", text).split())
    return text[:25] if len(text) >= 3 else fallback


def _phone(value: str | None) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if digits.startswith("51") and len(digits) == 11:
        digits = digits[2:]
    return digits[-9:] if len(digits) >= 9 else "999999999"


def summarize_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Reduce una respuesta del SDK, de la IPN o de la consulta de orden (todas
    con la misma forma: code, response.order[], response.customFields[]) a lo
    que necesita el pedido.

    status: "paid" si el codigo es 00 y hay una orden autorizada, "failed" si
    Izipay respondio con otro codigo, None si no se puede saber.
    """
    response = payload.get("response") if isinstance(payload.get("response"), dict) else {}
    orders = [o for o in (response.get("order") or []) if isinstance(o, dict)]
    first = orders[0] if orders else {}
    custom = {
        str(f.get("name")): str(f.get("value") or "")
        for f in (response.get("customFields") or [])
        if isinstance(f, dict)
    }
    code = str(payload.get("code") or "").strip()

    status: str | None = None
    if code == CODE_APPROVED and first:
        status = "paid"
    elif code and code not in UNSIGNED_CODES:
        status = "failed"

    return {
        "code": code,
        "status": status,
        "order_number": str(first.get("orderNumber") or "").strip(),
        "courier_order_id": custom.get(ORDER_FIELD) or None,
        "amount": amount_to_centimos(first.get("amount")),
        "currency": str(first.get("currency") or "").upper(),
        "pay_method": first.get("payMethodAuthorization") or response.get("payMethod"),
        "unique_id": first.get("uniqueId"),
        "authorization_code": first.get("codeAuth"),
        "reference_number": first.get("referenceNumber"),
        "date_transaction": first.get("dateTransaction"),
        "state_message": first.get("stateMessage"),
        "message": payload.get("messageUser") or payload.get("message"),
        "transaction_id": payload.get("transactionId"),
    }


class IzipaySdkService:
    """Cobros con el SDK de Izipay (boton de pagos)."""

    def __init__(self) -> None:
        self.merchant_code = str(settings.izipay_sdk_merchant_code or "").strip()
        self.api_key = str(settings.izipay_sdk_api_key or "").strip()
        self.hash_key = str(settings.izipay_sdk_hash_key or "").strip()
        self.rsa_public_key = str(settings.izipay_sdk_rsa_public_key or "").strip()
        self.ruc = str(settings.izipay_sdk_ruc or "").strip()
        self.ipn_url = str(settings.izipay_sdk_ipn_url or "").strip()
        env = str(settings.izipay_sdk_env or "sandbox").strip().lower()
        self.env = env if env in IZIPAY_SDK_URLS else "sandbox"
        self.api_url = IZIPAY_SDK_URLS[self.env]["api"]
        self.checkout_js = IZIPAY_SDK_URLS[self.env]["checkout_js"]

    @property
    def configured(self) -> bool:
        return bool(self.merchant_code and self.api_key and self.hash_key and self.rsa_public_key)

    @property
    def mode(self) -> str:
        return "TEST" if self.env == "sandbox" else "PRODUCTION"

    def _post(self, path: str, body: Dict[str, Any], transaction_id: str, token: str | None = None) -> Dict[str, Any]:
        headers = {"Content-Type": "application/json", "Accept": "application/json", "transactionId": transaction_id}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            response = requests.post(f"{self.api_url}{path}", headers=headers, json=body, timeout=20)
        except requests.RequestException as exc:
            raise IzipaySdkUnavailable(f"No se pudo conectar con Izipay: {exc}") from exc
        try:
            data = response.json()
        except ValueError as exc:
            raise IzipaySdkUnavailable(f"Izipay respondio {response.status_code} sin JSON.") from exc
        if not isinstance(data, dict):
            raise IzipaySdkUnavailable("Respuesta de Izipay inesperada.")
        return data

    def generar_token(
        self, *, order_number: str, monto: int, transaction_id: str, request_source: str = "ECOMMERCE"
    ) -> str:
        """Token de sesion: un solo uso, vale 15 minutos."""
        if not self.configured:
            raise IzipaySdkUnavailable("Faltan las claves del SDK de Izipay en el backend.")
        data = self._post(
            "/security/v1/Token/Generate",
            {
                "requestSource": request_source,
                "merchantCode": self.merchant_code,
                "orderNumber": order_number,
                "publicKey": self.api_key,
                "amount": centimos_to_amount(monto),
            },
            transaction_id,
        )
        token = (data.get("response") or {}).get("token") if isinstance(data.get("response"), dict) else None
        if str(data.get("code")) != CODE_APPROVED or not token:
            raise IzipaySdkUnavailable(f"Izipay no genero el token: {data.get('message') or data.get('code')}")
        return str(token)

    def crear_checkout(
        self,
        *,
        courier_order_id: str,
        monto: int,
        email: str | None,
        nombre: str | None,
        telefono: str | None,
        cliente_id: str | None,
    ) -> Dict[str, Any]:
        """
        Crea el token de sesion y la configuracion del checkout para el
        navegador. La web solo agrega el contenedor donde se muestra.
        """
        if monto <= 0 or monto > IZIPAY_SDK_MAX_ORDER_CENTIMOS:
            return {"exito": False, "mensaje": "El monto del pedido no es valido para Izipay.", "error_tipo": "validation"}

        order_number = izipay_order_number(courier_order_id)
        transaction_id = new_transaction_id()
        try:
            token = self.generar_token(order_number=order_number, monto=monto, transaction_id=transaction_id)
        except IzipaySdkUnavailable as exc:
            logger.error("izipay_sdk_token_failed order=%s: %s", courier_order_id, exc)
            return {"exito": False, "mensaje": str(exc), "error_tipo": "unavailable"}

        nombres = " ".join(str(nombre or "").split()).split(" ")
        billing: Dict[str, Any] = {
            "firstName": _plain_name(nombres[0] if nombres else "", "Cliente"),
            "lastName": _plain_name(" ".join(nombres[1:]), "ACME"),
            "phoneNumber": _phone(telefono),
            "street": settings.izipay_sdk_billing_street,
            "city": _ascii_letters(settings.izipay_sdk_billing_city, "Huancavelica"),
            "state": _ascii_letters(settings.izipay_sdk_billing_state, "Huancavelica"),
            "country": "PE",
            "postalCode": settings.izipay_sdk_billing_postal_code,
        }
        if email and "@" in email:
            billing["email"] = str(email).strip().lower()[:50]

        config: Dict[str, Any] = {
            "transactionId": transaction_id,
            "action": "pay",
            "merchantCode": self.merchant_code,
            "order": {
                "orderNumber": order_number,
                "currency": "PEN",
                "amount": centimos_to_amount(monto),
                "processType": "AT",
                "merchantBuyerId": str(cliente_id or courier_order_id).replace("-", "")[:100],
                "dateTimeTransaction": str(int(time.time() * 1_000_000)),
                "payMethod": settings.izipay_sdk_pay_methods,
            },
            "billing": billing,
            "language": {"init": "ESP", "showControlMultiLang": False},
            "customFields": [{"name": ORDER_FIELD, "value": str(courier_order_id)}],
        }
        if self.ipn_url:
            config["urlIPN"] = self.ipn_url

        return {
            "exito": True,
            "order_number": order_number,
            "transaction_id": transaction_id,
            "authorization": token,
            "key_rsa": self.rsa_public_key,
            "script_url": self.checkout_js,
            "config": config,
            "mode": self.mode,
            "monto_centimos": int(monto),
            "mensaje": "Checkout Izipay listo",
        }

    def verificar_firma(self, payload_http: str, signature: str) -> bool:
        """HMAC-SHA-256 de payloadHttp con la clave hash, en base64."""
        if not payload_http or not signature or not self.hash_key:
            if not self.hash_key:
                logger.error("izipay_sdk_hash_key_missing")
            return False
        digest = hmac.new(self.hash_key.encode("utf-8"), payload_http.encode("utf-8"), hashlib.sha256).digest()
        expected = base64.b64encode(digest).decode("ascii")
        return hmac.compare_digest(expected, str(signature).strip())

    def leer_firmado(self, payload_http: str, signature: str) -> Dict[str, Any] | None:
        """Devuelve el payload firmado ya parseado, o None si la firma no es valida."""
        if not self.verificar_firma(payload_http, signature):
            return None
        try:
            data = json.loads(payload_http)
        except (TypeError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def consultar_orden(self, order_number: str) -> Dict[str, Any]:
        """
        Consulta en Izipay el resultado de un numero de pedido.

        Devuelve {"encontrada": False} si Izipay no tiene un pago para ese
        numero. Lanza IzipaySdkUnavailable si Izipay no responde, para no
        confundir "sin pago" con "no se sabe".
        """
        transaction_id = new_transaction_id()
        token = self.generar_token(order_number=order_number, monto=0, transaction_id=transaction_id)
        data = self._post(
            "/orderinfo/v1/Transaction/Search",
            {"merchantCode": self.merchant_code, "numberOrden": order_number, "language": "ESP"},
            transaction_id,
            token,
        )
        payload_http = data.get("payloadHttp")
        signed = self.leer_firmado(payload_http, data.get("signature") or "") if payload_http else None
        # La consulta va por HTTPS directo a Izipay; si ademas trae firma, se usa
        # el contenido firmado.
        source = signed or data
        resumen = summarize_payload(source)
        if resumen["status"] == "paid":
            return {"encontrada": True, "resumen": resumen, "respuesta_completa": source}
        return {"encontrada": False, "resumen": resumen, "respuesta_completa": source}

    def reembolsar(self, resumen: Dict[str, Any], monto: int) -> Dict[str, Any]:
        """
        Devuelve un pago aprobado. Izipay pide el RUC del comercio y los datos
        de la autorizacion original (uniqueId, codigo y numero de referencia).
        """
        if not self.ruc:
            return {"exito": False, "mensaje": "Falta IZIPAY_SDK_RUC para hacer devoluciones con Izipay."}
        transaction_id = new_transaction_id()
        try:
            token = self.generar_token(
                order_number=resumen["order_number"], monto=monto, transaction_id=transaction_id, request_source="REFUND"
            )
            data = self._post(
                "/refund/v1/Transaction/Refund",
                {
                    "merchantCode": self.merchant_code,
                    "ruc": self.ruc,
                    "idUnique": str(resumen.get("unique_id") or ""),
                    "order": {
                        "authorizationCode": str(resumen.get("authorization_code") or ""),
                        "referenceNumber": str(resumen.get("reference_number") or ""),
                        "currency": "PEN",
                        "refundAmount": centimos_to_amount(monto),
                    },
                    "language": "ESP",
                },
                transaction_id,
                token,
            )
        except IzipaySdkUnavailable as exc:
            return {"exito": False, "mensaje": str(exc)}
        result = ((data.get("response") or {}).get("result") or {}) if isinstance(data.get("response"), dict) else {}
        if str(data.get("code")) != CODE_APPROVED:
            return {"exito": False, "mensaje": data.get("message") or "Izipay rechazo la devolucion", "respuesta_completa": data}
        return {"exito": True, "refund_id": result.get("refundTransactionId"), "respuesta_completa": data}
