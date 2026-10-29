from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import logging

import requests

from config import settings

logger = logging.getLogger(__name__)


class SupabaseCourierError(Exception):
    pass


_ONLINE_PAYMENT_METHOD_ID_CACHE: str | None = None
_ONLINE_PAYMENT_METHOD_ID_LOADED = False


class SupabaseCourierPayments:
    """Persistencia real del flujo courier sobre tablas Supabase."""

    def __init__(self) -> None:
        if not settings.supabase_url:
            raise SupabaseCourierError("Falta SUPABASE_URL en el backend.")

        self.base_url = f"{settings.supabase_url.rstrip('/')}/rest/v1"
        self.api_key = settings.supabase_service_role_key or settings.supabase_key or settings.supabase_anon_jwt
        self.default_bearer = settings.supabase_service_role_key or settings.supabase_anon_jwt or settings.supabase_key

        if not self.api_key:
            raise SupabaseCourierError("Falta SUPABASE_SERVICE_ROLE_KEY o SUPABASE_ANON_KEY para Supabase.")

    def _headers(self, bearer_token: str | None = None, prefer: str | None = None) -> dict[str, str]:
        token = settings.supabase_service_role_key or bearer_token or self.default_bearer
        headers = {
            "apikey": self.api_key,
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if prefer:
            headers["Prefer"] = prefer
        return headers

    def _request(
        self,
        method: str,
        table: str,
        *,
        bearer_token: str | None = None,
        params: dict[str, str] | None = None,
        json: Any | None = None,
        prefer: str | None = None,
    ) -> Any:
        response = requests.request(
            method,
            f"{self.base_url}/{table}",
            headers=self._headers(bearer_token, prefer),
            params=params,
            json=json,
            timeout=15,
        )
        if response.status_code >= 400:
            detail = response.text[:500]
            raise SupabaseCourierError(f"Supabase {table} devolvio {response.status_code}: {detail}")

        if not response.text:
            return None
        return response.json()

    # Pedidos sin pagar (supabase/migrations/202610290001_expire_unpaid_orders.sql
    # en ACME-WEB): vencen solos y el cliente puede cancelarlos.

    def expire_unpaid_order(self, order_id: str) -> int:
        """Cancela el pedido (y su grupo) si su plazo para pagar ya paso. Devuelve cuantos cancelo."""
        return int(self._request("POST", "rpc/expire_unpaid_orders", json={"p_order_id": order_id}) or 0)

    def cancel_unpaid_order(self, order_id: str, reason: str | None = None) -> dict[str, Any]:
        """Cancela el pedido sin pagar a pedido del cliente; devuelve sus productos."""
        return self._request(
            "POST",
            "rpc/cancel_my_unpaid_order",
            json={"p_order_id": order_id, "p_reason": reason},
        ) or {}

    def revive_expired_order(self, order_id: str) -> bool:
        """Devuelve a pending_payment un pedido que vencio el sistema hace poco y cuyo pago llego igual."""
        return bool(self._request("POST", "rpc/revive_expired_unpaid_order", json={"p_order_id": order_id}))

    def get_order(self, order_id: str, bearer_token: str | None = None) -> dict[str, Any]:
        rows = self._request(
            "GET",
            "orders",
            bearer_token=bearer_token,
            params={
                "select": "id,order_code,customer_id,total,currency,payment_status,payment_method_id,status,order_group_id,group_position,quote_id",
                "id": f"eq.{order_id}",
                "limit": "1",
            },
        )
        if not rows:
            raise SupabaseCourierError("Pedido courier no encontrado en Supabase.")
        order = rows[0]
        if order.get("order_group_id") and int(order.get("group_position") or 1) != 1:
            # Los pedidos de los otros locales no se pagan solos: se cobran con
            # el ancla. Sin monto, cualquier intento de cobro se rechaza.
            order["order_total"] = order.get("total")
            order["total"] = 0
        elif order.get("order_group_id"):
            # Pedido de varios locales: se cobra el grupo entero desde el ancla.
            groups = self._request(
                "GET",
                "order_groups",
                bearer_token=bearer_token,
                params={"select": "total", "id": f"eq.{order['order_group_id']}", "limit": "1"},
            )
            if groups:
                order["order_total"] = order.get("total")
                order["total"] = groups[0].get("total")
        return order

    def get_quote_payment_method(self, quote_id: str | None, bearer_token: str | None = None) -> str | None:
        """Medio de pago con el que se cotizo el pedido (la comision del total depende de el)."""
        if not quote_id:
            return None
        rows = self._request(
            "GET",
            "order_quotes",
            bearer_token=bearer_token,
            params={"select": "payment_method", "id": f"eq.{quote_id}", "limit": "1"},
        )
        return str(rows[0].get("payment_method") or "") or None if rows else None

    def get_profile(self, user_id: str | None, bearer_token: str | None = None) -> dict[str, Any] | None:
        if not user_id:
            return None

        rows = self._request(
            "GET",
            "profiles",
            bearer_token=bearer_token,
            params={
                "select": "user_id,full_name,email,phone",
                "user_id": f"eq.{user_id}",
                "limit": "1",
            },
        )
        return rows[0] if rows else None

    def get_online_payment_method_id(self, bearer_token: str | None = None) -> str | None:
        global _ONLINE_PAYMENT_METHOD_ID_CACHE, _ONLINE_PAYMENT_METHOD_ID_LOADED

        if _ONLINE_PAYMENT_METHOD_ID_LOADED:
            return _ONLINE_PAYMENT_METHOD_ID_CACHE

        rows = self._request(
            "GET",
            "payment_methods",
            bearer_token=bearer_token,
            params={
                "select": "id,code,name,is_online,is_active,created_at",
                "is_active": "eq.true",
                "is_online": "eq.true",
                "order": "created_at.asc",
            },
        )
        if not rows:
            _ONLINE_PAYMENT_METHOD_ID_LOADED = True
            _ONLINE_PAYMENT_METHOD_ID_CACHE = None
            return None

        preferred_codes = ("izipay", "culqi", "culqi_online", "card_online", "yape")
        by_code = {str(row.get("code", "")).lower(): row for row in rows}
        for code in preferred_codes:
            if code in by_code:
                _ONLINE_PAYMENT_METHOD_ID_CACHE = str(by_code[code]["id"])
                _ONLINE_PAYMENT_METHOD_ID_LOADED = True
                return _ONLINE_PAYMENT_METHOD_ID_CACHE
        _ONLINE_PAYMENT_METHOD_ID_CACHE = str(rows[0]["id"])
        _ONLINE_PAYMENT_METHOD_ID_LOADED = True
        return _ONLINE_PAYMENT_METHOD_ID_CACHE

    def find_pending_payment(
        self,
        order_id: str,
        bearer_token: str | None = None,
        provider: str = "culqi",
    ) -> dict[str, Any] | None:
        rows = self._request(
            "GET",
            "payments",
            bearer_token=bearer_token,
            params={
                "select": "id,order_id,status,external_reference,payment_method_id",
                "order_id": f"eq.{order_id}",
                "provider": f"eq.{provider}",
                "status": "eq.pending",
                "order": "requested_at.desc",
                "limit": "1",
            },
        )
        return rows[0] if rows else None

    def upsert_pending_payment(
        self,
        order: dict[str, Any],
        *,
        payment_method_id: str | None,
        external_reference: str,
        bearer_token: str | None = None,
        provider: str = "culqi",
    ) -> str:
        now = datetime.now(timezone.utc).isoformat()
        existing = self.find_pending_payment(str(order["id"]), bearer_token, provider)
        payload = {
            "order_id": order["id"],
            "customer_id": order.get("customer_id"),
            "payment_method_id": payment_method_id,
            "amount": float(order.get("total") or 0),
            "currency": order.get("currency") or "PEN",
            "status": "pending",
            "provider": provider,
            "external_reference": external_reference,
            "requested_at": now,
            "authorized_at": None,
            "captured_at": None,
            "failed_at": None,
            "updated_at": now,
        }

        if existing:
            rows = self._request(
                "PATCH",
                "payments",
                bearer_token=bearer_token,
                params={"id": f"eq.{existing['id']}"},
                json=payload,
                prefer="return=representation",
            )
            return str(rows[0]["id"])

        rows = self._request(
            "POST",
            "payments",
            bearer_token=bearer_token,
            json={
                "id": str(uuid4()),
                **payload,
                "created_at": now,
            },
            prefer="return=representation",
        )
        return str(rows[0]["id"])

    def update_order_payment(
        self,
        order_id: str,
        *,
        payment_method_id: str | None,
        payment_status: str,
        bearer_token: str | None = None,
    ) -> None:
        self._request(
            "PATCH",
            "orders",
            bearer_token=bearer_token,
            params={"id": f"eq.{order_id}"},
            json={
                "payment_method_id": payment_method_id,
                "payment_status": payment_status,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    def release_paid_order(self, order_id: str, bearer_token: str | None = None) -> bool:
        """Libera el pedido pagado y, si junto varios locales, los pedidos de los demas."""
        released = self._release_one_paid_order(order_id, bearer_token)
        try:
            self._release_group_orders(order_id, bearer_token)
        except SupabaseCourierError as exc:
            logger.error("release_paid_order: no se pudieron liberar los otros locales de %s: %s", order_id, exc)
        return released

    def _release_group_orders(self, order_id: str, bearer_token: str | None = None) -> None:
        """Un pedido de varios locales (order_groups) se paga una sola vez, desde
        el ancla; los pedidos de los demas locales pasan a pagados y a
        operaciones junto con el, y el grupo queda confirmado."""
        rows = self._request(
            "GET",
            "orders",
            bearer_token=bearer_token,
            params={"select": "id,order_group_id,payment_method_id", "id": f"eq.{order_id}", "limit": "1"},
        )
        if not rows or not rows[0].get("order_group_id"):
            return
        main = rows[0]
        group_id = main["order_group_id"]
        siblings = self._request(
            "GET",
            "orders",
            bearer_token=bearer_token,
            params={
                "select": "id,payment_status",
                "order_group_id": f"eq.{group_id}",
                "id": f"neq.{order_id}",
            },
        ) or []
        for sibling in siblings:
            sibling_id = str(sibling["id"])
            if sibling.get("payment_status") != "paid":
                self.update_order_payment(
                    sibling_id,
                    payment_method_id=main.get("payment_method_id"),
                    payment_status="paid",
                    bearer_token=bearer_token,
                )
            self._release_one_paid_order(sibling_id, bearer_token)
        self._request(
            "PATCH",
            "order_groups",
            bearer_token=bearer_token,
            params={"id": f"eq.{group_id}"},
            json={
                "payment_status": "paid",
                "status": "confirmed",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    def _release_one_paid_order(self, order_id: str, bearer_token: str | None = None) -> bool:
        """
        Pasa a "placed" un pedido que esperaba pago, recien ahora que el pago se
        confirmo, para que entre a la cola de operaciones. Solo toca pedidos en
        pending_payment, asi que repetirlo no hace nada.
        """
        now = datetime.now(timezone.utc).isoformat()
        try:
            rows = self._request(
                "PATCH",
                "orders",
                bearer_token=bearer_token,
                params={"id": f"eq.{order_id}", "status": "eq.pending_payment"},
                json={"status": "placed", "placed_at": now, "updated_at": now},
                prefer="return=representation",
            )
        except SupabaseCourierError as exc:
            # Base sin el estado pending_payment: el pedido ya esta en "placed" y
            # operaciones lo libera solo por payment_status.
            if "invalid input value for enum" in str(exc).lower():
                return False
            raise
        if not rows:
            return False

        try:
            self._request(
                "POST",
                "order_status_history",
                bearer_token=bearer_token,
                json={
                    "id": str(uuid4()),
                    "order_id": order_id,
                    "from_status": "pending_payment",
                    "to_status": "placed",
                    "actor_type": "system",
                    "note": "Pago confirmado, pedido enviado a operaciones",
                    "created_at": now,
                },
                prefer="return=minimal",
            )
        except SupabaseCourierError as exc:
            # El historial es informativo: el pedido ya quedo liberado.
            logger.warning("release_paid_order: no se pudo guardar el historial de %s: %s", order_id, exc)
        return True

    def update_payment_after_charge(
        self,
        payment_id: str,
        *,
        status: str,
        external_reference: str | None,
        bearer_token: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        payload: dict[str, Any] = {
            "status": status,
            "external_reference": external_reference,
            "updated_at": now,
        }
        if status == "paid":
            payload["authorized_at"] = now
            payload["captured_at"] = now
            payload["failed_at"] = None
        elif status == "failed":
            payload["failed_at"] = now

        self._request(
            "PATCH",
            "payments",
            bearer_token=bearer_token,
            params={"id": f"eq.{payment_id}"},
            json=payload,
        )

    def insert_transaction(
        self,
        *,
        payment_id: str,
        transaction_type: str,
        amount: float,
        status: str,
        provider_transaction_id: str | None,
        request_json: dict[str, Any] | None,
        response_json: dict[str, Any] | None,
        bearer_token: str | None = None,
    ) -> None:
        self._request(
            "POST",
            "payment_transactions",
            bearer_token=bearer_token,
            json={
                "id": str(uuid4()),
                "payment_id": payment_id,
                "transaction_type": transaction_type,
                "amount": amount,
                "provider_transaction_id": provider_transaction_id,
                "status": status,
                "request_json": request_json,
                "response_json": response_json,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    # ------------------------------------------------------------------
    # payment_attempts - idempotencia
    # ------------------------------------------------------------------

    def get_paid_payment_attempt(self, order_id: str, bearer_token: str | None = None) -> dict[str, Any] | None:
        """Devuelve el primer payment_attempt con status='paid' para el order_id dado."""
        rows = self._request(
            "GET",
            "payment_attempts",
            bearer_token=bearer_token,
            params={
                "select": "id,order_id,idempotency_key,provider_payment_id,status,metadata",
                "order_id": f"eq.{order_id}",
                "status": "eq.paid",
                "order": "created_at.desc",
                "limit": "1",
            },
        )
        return rows[0] if rows else None

    def create_payment_attempt(
        self,
        *,
        order_id: str,
        idempotency_key: str,
        amount: float,
        status: str = "pending",
        bearer_token: str | None = None,
    ) -> str:
        """Crea un payment_attempt nuevo. Si la idempotency_key ya existe, no falla (ignora el error)."""
        now = datetime.now(timezone.utc).isoformat()
        attempt_id = str(uuid4())
        try:
            self._request(
                "POST",
                "payment_attempts",
                bearer_token=bearer_token,
                json={
                    "id": attempt_id,
                    "order_id": order_id,
                    "provider": "culqi",
                    "idempotency_key": idempotency_key,
                    "amount": amount,
                    "status": status,
                    "created_at": now,
                    "updated_at": now,
                },
                prefer="return=minimal",
            )
        except SupabaseCourierError:
            # La clave de idempotencia ya existe u otro error menor; continuar
            pass
        return attempt_id

    def update_payment_attempt(
        self,
        *,
        idempotency_key: str,
        provider_payment_id: str | None,
        status: str,
        metadata: Any | None = None,
        bearer_token: str | None = None,
    ) -> None:
        """Actualiza el payment_attempt por idempotency_key con el resultado del cargo."""
        now = datetime.now(timezone.utc).isoformat()
        try:
            self._request(
                "PATCH",
                "payment_attempts",
                bearer_token=bearer_token,
                params={"idempotency_key": f"eq.{idempotency_key}"},
                json={
                    "status": status,
                    "provider_payment_id": provider_payment_id,
                    "metadata": metadata,
                    "updated_at": now,
                },
            )
        except SupabaseCourierError:
            # No bloquear el flujo principal si falla la actualizacion del intento
            pass
