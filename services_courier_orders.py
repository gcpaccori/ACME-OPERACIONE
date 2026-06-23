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

    def _rpc(
        self,
        name: str,
        *,
        bearer_token: str | None = None,
        json: Any | None = None,
    ) -> Any:
        response = requests.post(
            f"{self.base_url}/rpc/{name}",
            headers=self._headers(bearer_token),
            json=json,
            timeout=15,
        )
        if response.status_code >= 400:
            detail = response.text[:500]
            raise SupabaseOrderError(f"Supabase rpc/{name} devolvio {response.status_code}: {detail}")

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
        Usa la RPC create_order_from_quote para que orders, order_items,
        order_delivery_details, order_status_history y order_quotes se escriban
        de forma atomica contra el esquema real de Supabase.
        """
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

        items_payload: list[dict[str, Any]] = []
        for item in quote.get("items_snapshot") or []:
            unit_price = float(item.get("unit_price", item.get("customer_unit_price", 0)) or 0)
            items_payload.append({
                "product_id": item.get("product_id"),
                "product_name_snapshot": item.get("product_name_snapshot") or item.get("product_name", ""),
                "unit_price": unit_price,
                "quantity": int(item.get("quantity", 1) or 1),
                "notes": item.get("notes"),
                "line_total": float(item.get("line_total", 0) or 0),
                "customer_unit_price": float(item.get("customer_unit_price", unit_price) or 0),
                "merchant_unit_price": float(item.get("merchant_unit_price", unit_price) or 0),
                "platform_margin": float(item.get("platform_margin", 0) or 0),
            })

        rpc_payload = {
            "p_quote_id": quote.get("id"),
            "p_customer_id": customer_id,
            "p_merchant_id": merchant_id,
            "p_branch_id": branch_id,
            "p_fulfillment_type": fulfillment_type,
            "p_special_instructions": delivery_data.get("special_instructions"),
            "p_payment_method_id": None,
            "p_address_id": address_id,
            "p_address_snapshot": addr.get("line1") if addr else None,
            "p_reference_snapshot": addr.get("reference") if addr else None,
            "p_district_snapshot": addr.get("district") if addr else None,
            "p_city_snapshot": addr.get("city") if addr else None,
            "p_region_snapshot": addr.get("region") if addr else None,
            "p_recipient_name": delivery_data.get("recipient_name"),
            "p_recipient_phone": delivery_data.get("recipient_phone"),
            "p_lat": addr.get("lat") if addr else None,
            "p_lng": addr.get("lng") if addr else None,
            "p_items": items_payload,
        }

        result = self._rpc(
            "create_order_from_quote",
            bearer_token=bearer_token,
            json=rpc_payload,
        ) or {}

        return {
            "order_id": str(result.get("order_id") or ""),
            "order_code": int(result.get("order_code") or 0),
            "total": float(result.get("total") or quote.get("total", 0)),
        }
