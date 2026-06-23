from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4
import random
import logging

import requests

from config import settings

logger = logging.getLogger(__name__)


class SupabaseOrderError(Exception):
    pass


class SupabaseOrderService:
    """Persistencia de pedidos courier sobre tablas Supabase."""

    def __init__(self) -> None:
        if not settings.supabase_url:
            raise SupabaseOrderError("Falta SUPABASE_URL en el backend.")

        self.base_url = f"{settings.supabase_url.rstrip('/')}/rest/v1"
        self.api_key = settings.supabase_service_role_key or settings.supabase_key or settings.supabase_anon_jwt
        self.default_bearer = settings.supabase_service_role_key or settings.supabase_anon_jwt or settings.supabase_key

        if not self.api_key:
            raise SupabaseOrderError("Falta SUPABASE_SERVICE_ROLE_KEY o SUPABASE_ANON_KEY para Supabase.")

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
            raise SupabaseOrderError(f"Supabase {table} devolvio {response.status_code}: {detail}")

        if not response.text:
            return None
        return response.json()

    def get_active_quote(self, quote_id: str, customer_id: str, bearer_token: str | None = None) -> dict[str, Any] | None:
        now_iso = datetime.now(timezone.utc).isoformat()
        rows = self._request(
            "GET",
            "order_quotes",
            bearer_token=bearer_token,
            params={
                "select": "*",
                "id": f"eq.{quote_id}",
                "customer_id": f"eq.{customer_id}",
                "status": "eq.active",
                "expires_at": f"gt.{now_iso}",
                "limit": "1",
            },
        )
        return rows[0] if rows else None

    def _next_order_code(self) -> int:
        """Intenta obtener el siguiente order_code de la secuencia; fallback a random 6 digitos."""
        try:
            # Llamar a una funcion RPC de Supabase si existe
            response = requests.post(
                f"{self.base_url}/rpc/next_order_code",
                headers=self._headers(),
                timeout=10,
            )
            if response.status_code == 200 and response.text:
                return int(response.json())
        except Exception:
            pass
        # Fallback: 6 digitos aleatorios
        return random.randint(100000, 999999)

    def create_order_from_quote(
        self,
        quote: dict[str, Any],
        delivery_data: dict[str, Any],
        customer_id: str,
        bearer_token: str | None = None,
    ) -> dict[str, Any]:
        """
        Crea el pedido completo en Supabase a partir de una cotizacion validada.
        Ejecuta: address insert, order insert, order_items insert,
                 order_delivery_details insert, order_status_history insert,
                 y marca la cotizacion como 'used'.
        """
        now = datetime.now(timezone.utc).isoformat()
        fulfillment_type = delivery_data.get("fulfillment_type", "delivery")

        # 1. INSERT en addresses (si delivery y hay datos de direccion)
        address_id: str | None = None
        addr = delivery_data.get("address")
        if fulfillment_type == "delivery" and addr:
            address_id = str(uuid4())
            addr_payload = {
                "id": address_id,
                "line1": addr.get("line1", ""),
                "line2": addr.get("line2"),
                "reference": addr.get("reference"),
                "district": addr.get("district"),
                "city": addr.get("city"),
                "region": addr.get("region"),
                "country": addr.get("country", "Peru"),
                "lat": addr.get("lat"),
                "lng": addr.get("lng"),
            }
            try:
                self._request(
                    "POST",
                    "addresses",
                    bearer_token=bearer_token,
                    json=addr_payload,
                    prefer="return=minimal",
                )
            except SupabaseOrderError as exc:
                logger.warning("No se pudo crear la direccion: %s", exc)
                address_id = None

        # 2. INSERT en orders
        order_id = str(uuid4())
        order_code = self._next_order_code()
        order_payload = {
            "id": order_id,
            "order_code": order_code,
            "customer_id": customer_id,
            "merchant_id": quote.get("merchant_id"),
            "branch_id": quote.get("branch_id"),
            "status": "placed",
            "payment_status": "pending",
            "subtotal": float(quote.get("subtotal", 0)),
            "discount": float(quote.get("discount", 0)),
            "delivery_fee": float(quote.get("delivery_fee", 0)),
            "service_fee": float(quote.get("service_fee", 0)),
            "tip_amount": float(quote.get("tip_amount", 0)),
            "total": float(quote.get("total", 0)),
            "currency": "PEN",
            "quote_id": quote.get("id"),
            "fulfillment_type": fulfillment_type,
            "special_instructions": delivery_data.get("special_instructions"),
            "placed_at": now,
            "created_at": now,
            "updated_at": now,
        }
        if address_id:
            order_payload["delivery_address_id"] = address_id

        order_rows = self._request(
            "POST",
            "orders",
            bearer_token=bearer_token,
            json=order_payload,
            prefer="return=representation",
        )
        if order_rows:
            created_order = order_rows[0]
        else:
            created_order = order_payload

        # 3. INSERT en order_items
        items_snapshot: list[dict[str, Any]] = quote.get("items_snapshot") or []
        for item in items_snapshot:
            item_payload = {
                "id": str(uuid4()),
                "order_id": order_id,
                "product_id": item.get("product_id"),
                "product_name_snapshot": item.get("product_name", ""),
                "unit_price": float(item.get("unit_price", 0)),
                "quantity": int(item.get("quantity", 1)),
                "notes": item.get("notes"),
                "line_total": float(item.get("line_total", 0)),
            }
            try:
                self._request(
                    "POST",
                    "order_items",
                    bearer_token=bearer_token,
                    json=item_payload,
                    prefer="return=minimal",
                )
            except SupabaseOrderError as exc:
                logger.warning("Error insertando order_item product=%s: %s", item.get("product_id"), exc)

        # 4. INSERT en order_delivery_details (si delivery)
        if fulfillment_type == "delivery":
            delivery_detail_payload = {
                "id": str(uuid4()),
                "order_id": order_id,
                "recipient_name": delivery_data.get("recipient_name"),
                "recipient_phone": delivery_data.get("recipient_phone"),
                "address_id": address_id,
                "created_at": now,
            }
            try:
                self._request(
                    "POST",
                    "order_delivery_details",
                    bearer_token=bearer_token,
                    json=delivery_detail_payload,
                    prefer="return=minimal",
                )
            except SupabaseOrderError as exc:
                logger.warning("No se pudo crear order_delivery_details: %s", exc)

        # 5. INSERT en order_status_history
        history_payload = {
            "id": str(uuid4()),
            "order_id": order_id,
            "status": "placed",
            "notes": "Pedido creado desde cotizacion",
            "created_at": now,
        }
        try:
            self._request(
                "POST",
                "order_status_history",
                bearer_token=bearer_token,
                json=history_payload,
                prefer="return=minimal",
            )
        except SupabaseOrderError as exc:
            logger.warning("No se pudo crear order_status_history: %s", exc)

        # 6. Marcar cotizacion como 'used'
        try:
            self._request(
                "PATCH",
                "order_quotes",
                bearer_token=bearer_token,
                params={"id": f"eq.{quote['id']}"},
                json={"status": "used"},
            )
        except SupabaseOrderError as exc:
            logger.warning("No se pudo marcar quote como usado: %s", exc)

        return {
            "order_id": order_id,
            "order_code": order_code,
            "total": float(quote.get("total", 0)),
        }
