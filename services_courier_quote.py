from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any
from uuid import uuid4
import math

import requests

from config import settings


class SupabaseQuoteError(Exception):
    pass


class SupabaseQuoteService:
    """Persistencia de cotizaciones courier sobre tablas Supabase."""

    def __init__(self) -> None:
        if not settings.supabase_url:
            raise SupabaseQuoteError("Falta SUPABASE_URL en el backend.")

        self.base_url = f"{settings.supabase_url.rstrip('/')}/rest/v1"
        self.api_key = settings.supabase_service_role_key or settings.supabase_key or settings.supabase_anon_jwt
        self.default_bearer = settings.supabase_service_role_key or settings.supabase_anon_jwt or settings.supabase_key

        if not self.api_key:
            raise SupabaseQuoteError("Falta SUPABASE_SERVICE_ROLE_KEY o SUPABASE_ANON_KEY para Supabase.")

    def _headers(self, bearer_token: str | None = None, prefer: str | None = None) -> dict[str, str]:
        token = bearer_token or self.default_bearer
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
            raise SupabaseQuoteError(f"Supabase {table} devolvio {response.status_code}: {detail}")

        if not response.text:
            return None
        return response.json()

    def get_branch(self, branch_id: str, bearer_token: str | None = None) -> dict[str, Any]:
        rows = self._request(
            "GET",
            "merchant_branches",
            bearer_token=bearer_token,
            params={
                "select": "id,merchant_id,lat,lng",
                "id": f"eq.{branch_id}",
                "limit": "1",
            },
        )
        if not rows:
            raise SupabaseQuoteError(f"Sucursal '{branch_id}' no encontrada en Supabase.")
        return rows[0]

    def get_products_with_prices(self, product_ids: list[str], bearer_token: str | None = None) -> list[dict[str, Any]]:
        if not product_ids:
            return []
        ids_csv = ",".join(product_ids)
        rows = self._request(
            "GET",
            "products",
            bearer_token=bearer_token,
            params={
                "select": "id,base_price,name",
                "id": f"in.({ids_csv})",
            },
        )
        return rows or []

    def get_modifiers_prices(self, modifier_ids: list[str], bearer_token: str | None = None) -> list[dict[str, Any]]:
        if not modifier_ids:
            return []
        ids_csv = ",".join(modifier_ids)
        rows = self._request(
            "GET",
            "modifier_options",
            bearer_token=bearer_token,
            params={
                "select": "id,price_delta",
                "id": f"in.({ids_csv})",
            },
        )
        return rows or []

    def save_quote(
        self,
        *,
        customer_id: str,
        branch_id: str,
        subtotal: float,
        discount: float,
        service_fee: float,
        service_fee_rate: float,
        delivery_fee: float,
        tip_amount: float,
        total: float,
        distance_km: float | None,
        payment_method: str,
        items_snapshot: list[dict[str, Any]],
        bearer_token: str | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=5)
        quote_id = str(uuid4())
        payload = {
            "id": quote_id,
            "customer_id": customer_id,
            "branch_id": branch_id,
            "subtotal": subtotal,
            "discount": discount,
            "service_fee": service_fee,
            "service_fee_rate": service_fee_rate,
            "delivery_fee": delivery_fee,
            "tip_amount": tip_amount,
            "total": total,
            "distance_km": distance_km,
            "payment_method": payment_method,
            "items_snapshot": items_snapshot,
            "status": "active",
            "expires_at": expires_at.isoformat(),
            "created_at": now.isoformat(),
        }
        rows = self._request(
            "POST",
            "order_quotes",
            bearer_token=bearer_token,
            json=payload,
            prefer="return=representation",
        )
        if rows:
            return rows[0]
        return payload

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

    def mark_quote_used(self, quote_id: str, bearer_token: str | None = None) -> None:
        self._request(
            "PATCH",
            "order_quotes",
            bearer_token=bearer_token,
            params={"id": f"eq.{quote_id}"},
            json={"status": "used"},
        )


# ---------------------------------------------------------------------------
# Calculos de negocio
# ---------------------------------------------------------------------------

def calculate_distance_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Formula de Haversine para distancia entre dos coordenadas en km."""
    R = 6371.0  # Radio de la Tierra en km
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lng2 - lng1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def calculate_delivery_fee(distance_km: float, fulfillment_type: str) -> float:
    """Tarifa de envio segun distancia y tipo de fulfillment."""
    if fulfillment_type == "pickup":
        return 0.0
    if distance_km <= 2.5:
        return 5.0
    if distance_km <= 4.0:
        return 7.0
    if distance_km <= 6.0:
        return 9.0
    raise ValueError("Fuera de cobertura: la distancia supera los 6 km.")


def calculate_service_fee(base_amount: float) -> float:
    """Tarifa de servicio = 3.6% del subtotal neto."""
    return round(base_amount * 0.036, 2)
