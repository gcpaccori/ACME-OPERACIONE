"""
Cliente de la API REST V4 de Izipay (formulario incrustado).

Sigue el flujo de los ejemplos oficiales de Izipay
(github.com/izipay-pe/Server-PaymentForm-Python-Flask):

1. El backend crea un formToken con Charge/CreatePayment, por el monto del
   pedido, y lo entrega al navegador junto a la clave publica.
2. El navegador muestra el formulario de Izipay con ese formToken. Al terminar,
   Izipay devuelve al navegador la respuesta firmada (kr-answer + kr-hash).
3. Izipay avisa tambien al backend, servidor a servidor, por la IPN. Esa es la
   confirmacion que manda: el navegador puede cerrarse antes de volver.

Las firmas se verifican con HMAC-SHA-256 sobre el texto de kr-answer. La que
llega al navegador se firma con la clave HMAC; la de la IPN, con la contrasena
de la API REST. El campo kr-hash-key dice cual se uso.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
from typing import Any, Dict

import requests

from config import settings

logger = logging.getLogger(__name__)

IZIPAY_MAX_ORDER_CENTIMOS = 700000
IZIPAY_ORDER_ID_PREFIX = "courier-"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class IzipayUnavailable(Exception):
    """Izipay no respondio o respondio algo que no se puede interpretar."""


def izipay_order_id(courier_order_id: str) -> str:
    """
    Id de orden que se manda a Izipay para un pedido. Es fijo por pedido: todos
    los intentos de pago del mismo pedido quedan bajo la misma orden, y
    Order/Get los devuelve juntos para saber si alguno ya se pago.
    """
    return f"{IZIPAY_ORDER_ID_PREFIX}{courier_order_id}"


def courier_order_id_from_izipay(order_id: str | None) -> str | None:
    raw = str(order_id or "").strip()
    if not raw.startswith(IZIPAY_ORDER_ID_PREFIX):
        return None
    return raw[len(IZIPAY_ORDER_ID_PREFIX):] or None


def _compact_text(value: str | None, fallback: str, max_length: int | None = None) -> str:
    text = " ".join(str(value or "").strip().split()) or fallback
    if max_length and len(text) > max_length:
        return text[:max_length].rstrip()
    return text


def _clean_phone(value: str | None) -> str | None:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[-15:] if len(digits) >= 6 else None


def _sign(key: str, kr_answer: str) -> str:
    return hmac.new(key.encode("utf-8"), kr_answer.encode("utf-8"), hashlib.sha256).hexdigest()


def summarize_answer(answer: Dict[str, Any]) -> Dict[str, Any]:
    """
    Reduce una respuesta de Izipay (V4/Payment del navegador o de la IPN, o
    V4/OrderTransactions de Order/Get) a lo que necesita el pedido.

    Devuelve el estado normalizado: "paid" si hay una transaccion pagada,
    "failed" si todas las transacciones fueron rechazadas, o None si sigue
    abierta (por ejemplo, todavia esperando la autenticacion 3DS).
    """
    transactions = [t for t in (answer.get("transactions") or []) if isinstance(t, dict)]
    details = answer.get("orderDetails") if isinstance(answer.get("orderDetails"), dict) else {}
    order_id = str(details.get("orderId") or answer.get("orderId") or "").strip()
    if not order_id:
        for t in transactions:
            t_details = t.get("orderDetails") if isinstance(t.get("orderDetails"), dict) else {}
            if t_details.get("orderId"):
                order_id = str(t_details["orderId"]).strip()
                break

    paid = [
        t for t in transactions
        if str(t.get("status") or "").upper() == "PAID"
        and str(t.get("operationType") or "DEBIT").upper() == "DEBIT"
    ]
    order_status = str(answer.get("orderStatus") or "").upper()

    status: str | None = None
    winner: Dict[str, Any] | None = None
    if paid:
        status, winner = "paid", paid[0]
    elif order_status == "PAID" and transactions:
        status, winner = "paid", transactions[0]
    elif transactions and all(
        str(t.get("status") or "").upper() == "UNPAID"
        and str(t.get("detailedStatus") or "").upper() in ("REFUSED", "ERROR", "CANCELLED", "ABANDONED", "EXPIRED")
        for t in transactions
    ):
        status, winner = "failed", transactions[0]
    elif order_status == "UNPAID" and transactions:
        status, winner = "failed", transactions[0]

    winner = winner or (transactions[0] if transactions else {})
    amount = winner.get("amount")
    if amount is None:
        amount = details.get("orderTotalAmount")
    currency = winner.get("currency") or details.get("orderCurrency")
    metadata = winner.get("metadata") if isinstance(winner.get("metadata"), dict) else {}
    if not metadata and isinstance(details.get("metadata"), dict):
        metadata = details["metadata"]

    return {
        "order_id": order_id,
        "status": status,
        "amount": int(amount or 0),
        "currency": str(currency or "").upper(),
        "transaction_uuid": winner.get("uuid"),
        "detailed_status": winner.get("detailedStatus"),
        "error_message": winner.get("detailedErrorMessage") or winner.get("errorMessage"),
        "mode": (details.get("mode") or answer.get("mode")),
        "metadata": metadata,
    }


class IzipayService:
    """Cobros con Izipay a traves de su API REST V4."""

    def __init__(self) -> None:
        self.username = str(settings.izipay_username or "").strip()
        self.password = str(settings.izipay_password or "").strip()
        self.public_key = str(settings.izipay_public_key or "").strip()
        self.hmac_key = str(settings.izipay_hmac_key or "").strip()
        self.api_url = str(settings.izipay_api_url or "https://api.micuentaweb.pe").rstrip("/")

    @property
    def configured(self) -> bool:
        return bool(self.username and self.password and self.public_key)

    @property
    def mode(self) -> str:
        return "TEST" if self.password.startswith("testpassword_") else "PRODUCTION"

    def _headers(self) -> Dict[str, str]:
        credentials = base64.b64encode(f"{self.username}:{self.password}".encode("utf-8")).decode("ascii")
        return {
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _request(self, web_service: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self.configured:
            raise IzipayUnavailable("Faltan las claves de Izipay en el backend.")
        try:
            response = requests.post(
                f"{self.api_url}/api-payment/V4/{web_service}",
                headers=self._headers(),
                json=payload,
                timeout=20,
            )
        except requests.RequestException as exc:
            raise IzipayUnavailable(f"No se pudo conectar con Izipay: {exc}") from exc
        try:
            data = response.json()
        except ValueError as exc:
            raise IzipayUnavailable(f"Izipay respondio {response.status_code} sin JSON.") from exc
        if not isinstance(data, dict):
            raise IzipayUnavailable("Respuesta de Izipay inesperada.")
        return data

    @staticmethod
    def _error_message(answer: Dict[str, Any]) -> str:
        message = answer.get("detailedErrorMessage") or answer.get("errorMessage") or "Izipay rechazo la solicitud"
        code = answer.get("errorCode")
        return f"{message} ({code})" if code else str(message)

    def crear_form_token(
        self,
        *,
        courier_order_id: str,
        monto: int,
        email: str,
        nombre: str,
        telefono: str | None,
        referencia_cliente: str | None,
    ) -> Dict[str, Any]:
        """Crea el formToken con el que el navegador muestra el formulario de pago."""
        clean_email = str(email or "").strip().lower()
        if not EMAIL_RE.match(clean_email):
            return {"exito": False, "mensaje": "El email del cliente no es valido para Izipay.", "error_tipo": "validation"}
        if monto <= 0 or monto > IZIPAY_MAX_ORDER_CENTIMOS:
            return {"exito": False, "mensaje": "El monto del pedido no es valido para Izipay.", "error_tipo": "validation"}

        nombres = _compact_text(nombre, "Cliente ACME").split()
        billing: Dict[str, Any] = {
            "firstName": _compact_text(nombres[0] if nombres else "", "Cliente", 60),
            "lastName": _compact_text(" ".join(nombres[1:]), "ACME", 60),
        }
        phone = _clean_phone(telefono)
        if phone:
            billing["cellPhoneNumber"] = phone

        order_id = izipay_order_id(courier_order_id)
        payload: Dict[str, Any] = {
            "amount": int(monto),
            "currency": "PEN",
            "orderId": order_id,
            "customer": {
                "email": clean_email,
                "billingDetails": billing,
            },
            "metadata": {"pedido_id": str(courier_order_id), "sistema": "ACME"},
        }
        if referencia_cliente:
            payload["customer"]["reference"] = str(referencia_cliente)[:80]

        try:
            data = self._request("Charge/CreatePayment", payload)
        except IzipayUnavailable as exc:
            logger.error("izipay_create_payment_unavailable order=%s: %s", order_id, exc)
            return {"exito": False, "mensaje": str(exc), "error_tipo": "unavailable"}

        answer = data.get("answer") or {}
        if data.get("status") != "SUCCESS" or not answer.get("formToken"):
            logger.warning(
                "izipay_create_payment_failed order=%s amount=%s code=%s message=%s",
                order_id, monto, answer.get("errorCode"), answer.get("errorMessage"),
            )
            return {
                "exito": False,
                "mensaje": self._error_message(answer),
                "respuesta_completa": answer,
                "error_tipo": "parameter_error" if str(answer.get("errorCode") or "").startswith("INT_") else None,
            }

        return {
            "exito": True,
            "order_id": order_id,
            "form_token": answer["formToken"],
            "public_key": self.public_key,
            "mode": data.get("mode") or self.mode,
            "monto_centimos": int(monto),
            "mensaje": "Formulario Izipay listo",
        }

    def obtener_orden(self, order_id: str) -> Dict[str, Any]:
        """
        Consulta en Izipay todos los intentos de pago de una orden.

        Devuelve {"encontrada": False} si Izipay no tiene transacciones para esa
        orden (el cliente nunca llego a pagar). Lanza IzipayUnavailable si
        Izipay no responde, para no confundir "sin pago" con "no se sabe".
        """
        data = self._request("Order/Get", {"orderId": order_id})
        answer = data.get("answer") or {}
        if data.get("status") != "SUCCESS":
            if answer.get("errorCode") == "PSP_010":
                return {"encontrada": False, "respuesta_completa": answer}
            raise IzipayUnavailable(self._error_message(answer))
        resumen = summarize_answer(answer)
        resumen["order_id"] = resumen.get("order_id") or order_id
        return {"encontrada": True, "resumen": resumen, "respuesta_completa": answer}

    def reembolsar(self, transaction_uuid: str, monto: int, motivo: str | None) -> Dict[str, Any]:
        """
        Devuelve un pago. Izipay anula la transaccion si aun no se liquido y la
        reembolsa si ya se liquido; Transaction/CancelOrRefund elige solo.
        """
        payload: Dict[str, Any] = {
            "uuid": transaction_uuid,
            "amount": int(monto),
            "currency": "PEN",
            "comment": _compact_text(motivo, "Reembolso ACME", 255),
        }
        data = self._request("Transaction/CancelOrRefund", payload)
        answer = data.get("answer") or {}
        if data.get("status") != "SUCCESS":
            return {"exito": False, "mensaje": self._error_message(answer), "respuesta_completa": answer}
        return {"exito": True, "refund_id": answer.get("uuid"), "respuesta_completa": answer}

    def verificar_firma(self, kr_answer: str, kr_hash: str, kr_hash_key: str | None = None) -> bool:
        """
        Comprueba que kr-answer viene de Izipay. "sha256_hmac" es la firma del
        navegador (clave HMAC); "password" es la de la IPN (contrasena REST).
        """
        if not kr_answer or not kr_hash:
            return False
        key_kind = str(kr_hash_key or "sha256_hmac").strip().lower()
        key = self.password if key_kind == "password" else self.hmac_key
        if not key:
            logger.error("izipay_signature_key_missing kind=%s", key_kind)
            return False
        return hmac.compare_digest(_sign(key, kr_answer), str(kr_hash).strip().lower())

    @staticmethod
    def leer_respuesta(kr_answer: str) -> Dict[str, Any]:
        try:
            data = json.loads(kr_answer)
        except (TypeError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}
