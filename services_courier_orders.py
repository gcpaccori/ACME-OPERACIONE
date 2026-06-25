from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4
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

    def get_branch(self, branch_id: str, bearer_token: str | None = None) -> dict[str, Any]:
        rows = self._request(
            "GET",
            "merchant_branches",
            bearer_token=bearer_token,
            params={
                "select": "id,merchant_id",
                "id": f"eq.{branch_id}",
                "limit": "1",
            },
        )
        if not rows:
            raise SupabaseOrderError(f"Sucursal '{branch_id}' no encontrada en Supabase.")
        return rows[0]

    def create_order_from_quote(
        self,
        quote: dict[str, Any],
        delivery_data: dict[str, Any],
        customer_id: str,
        bearer_token: str | None = None,
    ) -> dict[str, Any]:
        """
        Crea el pedido completo en Supabase a partir de una cotizacion validada.
        Escribe contra el esquema real de Supabase. La secuencia de order_code
        vive como DEFAULT en orders, asi que el backend no calcula correlativos.
        """
        now = datetime.now(timezone.utc).isoformat()
        fulfillment_type = delivery_data.get("fulfillment_type", "delivery")
        branch_id = str(quote.get("branch_id") or "")
        if not branch_id:
            raise SupabaseOrderError("La cotizacion no tiene branch_id.")

        branch = self.get_branch(branch_id, bearer_token)
        merchant_id = str(branch.get("merchant_id") or "")
        if not merchant_id:
            raise SupabaseOrderError(f"La sucursal '{branch_id}' no tiene merchant_id.")

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

        order_id = str(uuid4())
        order_payload = {
            "id": order_id,
            "customer_id": customer_id,
            "merchant_id": merchant_id,
            "branch_id": branch_id,
            "status": "placed",
            "payment_status": "pending",
            "subtotal": float(quote.get("subtotal", 0) or 0),
            "products_total": float(quote.get("subtotal", 0) or 0),
            "discount_total": float(quote.get("discount", 0) or 0),
            "delivery_fee": float(quote.get("delivery_fee", 0) or 0),
            "service_fee": float(quote.get("service_fee", 0) or 0),
            "service_fee_rate": float(quote.get("service_fee_rate", 0.036) or 0.036),
            "tip_amount": float(quote.get("tip_amount", 0) or 0),
            "tax_amount": 0,
            "total": float(quote.get("total", 0) or 0),
            "currency": "PEN",
            "quote_id": quote.get("id"),
            "fulfillment_type": fulfillment_type,
            "special_instructions": delivery_data.get("special_instructions"),
            "placed_at": now,
            "created_at": now,
            "updated_at": now,
        }

        order_rows = self._request(
            "POST",
            "orders",
            bearer_token=bearer_token,
            json=order_payload,
            prefer="return=representation",
        )
        created_order = order_rows[0] if order_rows else order_payload

        items_payload: list[dict[str, Any]] = []
        for item in quote.get("items_snapshot") or []:
            unit_price = float(item.get("unit_price", item.get("customer_unit_price", 0)) or 0)
            items_payload.append({
                "id": str(uuid4()),
                "order_id": order_id,
                "product_id": item.get("product_id"),
                "product_name_snapshot": item.get("product_name_snapshot") or item.get("product_name", ""),
                "unit_price": unit_price,
                "quantity": int(item.get("quantity", 1) or 1),
                "notes": item.get("notes"),
                "line_total": float(item.get("line_total", 0) or 0),
                "customer_unit_price": float(item.get("customer_unit_price", unit_price) or 0),
                "merchant_unit_price": float(item.get("merchant_unit_price", unit_price) or 0),
                "platform_margin": float(item.get("platform_margin", 0) or 0),
                "created_at": now,
            })

        if items_payload:
            self._request(
                "POST",
                "order_items",
                bearer_token=bearer_token,
                json=items_payload,
                prefer="return=minimal",
            )

        if fulfillment_type == "delivery" and addr:
            delivery_detail_payload = {
                "order_id": order_id,
                "address_id": address_id,
                "address_snapshot": addr.get("line1"),
                "reference_snapshot": addr.get("reference"),
                "district_snapshot": addr.get("district"),
                "city_snapshot": addr.get("city"),
                "region_snapshot": addr.get("region"),
                "lat": addr.get("lat"),
                "lng": addr.get("lng"),
                "estimated_distance_km": quote.get("distance_km"),
                "recipient_name": delivery_data.get("recipient_name"),
                "recipient_phone": delivery_data.get("recipient_phone"),
                "created_at": now,
                "updated_at": now,
            }
            self._request(
                "POST",
                "order_delivery_details",
                bearer_token=bearer_token,
                json=delivery_detail_payload,
                prefer="return=minimal",
            )

        history_payload = {
            "id": str(uuid4()),
            "order_id": order_id,
            "from_status": "placed",
            "to_status": "placed",
            "actor_user_id": customer_id,
            "actor_type": "customer",
            "note": f"Pedido creado desde cotizacion {quote.get('id')}",
            "created_at": now,
        }
        self._request(
            "POST",
            "order_status_history",
            bearer_token=bearer_token,
            json=history_payload,
            prefer="return=minimal",
        )

        self._request(
            "PATCH",
            "order_quotes",
            bearer_token=bearer_token,
            params={"id": f"eq.{quote['id']}"},
            json={"status": "used"},
        )

        return {
            "order_id": order_id,
            "order_code": int(created_order.get("order_code") or 0),
            "total": float(created_order.get("total") or quote.get("total", 0)),
        }
