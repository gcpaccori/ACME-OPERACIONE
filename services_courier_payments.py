from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import requests

from config import settings


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

    def get_order(self, order_id: str, bearer_token: str | None = None) -> dict[str, Any]:
        rows = self._request(
            "GET",
            "orders",
            bearer_token=bearer_token,
            params={
                "select": "id,order_code,customer_id,total,currency,payment_status,payment_method_id,status",
                "id": f"eq.{order_id}",
                "limit": "1",
            },
        )
        if not rows:
            raise SupabaseCourierError("Pedido courier no encontrado en Supabase.")
        return rows[0]

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

        preferred_codes = ("culqi", "culqi_online", "card_online", "yape")
        by_code = {str(row.get("code", "")).lower(): row for row in rows}
        for code in preferred_codes:
            if code in by_code:
                _ONLINE_PAYMENT_METHOD_ID_CACHE = str(by_code[code]["id"])
                _ONLINE_PAYMENT_METHOD_ID_LOADED = True
                return _ONLINE_PAYMENT_METHOD_ID_CACHE
        _ONLINE_PAYMENT_METHOD_ID_CACHE = str(rows[0]["id"])
        _ONLINE_PAYMENT_METHOD_ID_LOADED = True
        return _ONLINE_PAYMENT_METHOD_ID_CACHE

    def find_pending_payment(self, order_id: str, bearer_token: str | None = None) -> dict[str, Any] | None:
        rows = self._request(
            "GET",
            "payments",
            bearer_token=bearer_token,
            params={
                "select": "id,order_id,status,external_reference,payment_method_id",
                "order_id": f"eq.{order_id}",
                "provider": "eq.culqi",
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
    ) -> str:
        now = datetime.now(timezone.utc).isoformat()
        existing = self.find_pending_payment(str(order["id"]), bearer_token)
        payload = {
            "order_id": order["id"],
            "customer_id": order.get("customer_id"),
            "payment_method_id": payment_method_id,
            "amount": float(order.get("total") or 0),
            "currency": order.get("currency") or "PEN",
            "status": "pending",
            "provider": "culqi",
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
