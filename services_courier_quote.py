from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any
from uuid import uuid4
import logging
import math
import unicodedata

import requests

from config import METODO_YAPE_DIRECTO, payment_provider_for, settings, yape_propio_aplica
from services_courier_tariffs import TariffZoneOverride, extract_zone_code

logger = logging.getLogger(__name__)


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
                "select": "id,base_price,name,merchant_id,category_id",
                "id": f"in.({ids_csv})",
            },
        )
        return rows or []

    def get_categories(self, category_ids: list[str], bearer_token: str | None = None) -> list[dict[str, Any]]:
        ids = [cid for cid in dict.fromkeys(category_ids) if cid]
        if not ids:
            return []
        ids_csv = ",".join(ids)
        rows = self._request(
            "GET",
            "categories",
            bearer_token=bearer_token,
            params={
                "select": "id,name",
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

    def get_delivery_zone_overrides(self, branch_id: str, bearer_token: str | None = None) -> dict[str, TariffZoneOverride]:
        rows = self._request(
            "GET",
            "branch_delivery_zones",
            bearer_token=bearer_token,
            params={
                "select": "zone_id,fee_override,is_active",
                "branch_id": f"eq.{branch_id}",
                "is_active": "eq.true",
            },
        ) or []
        zone_ids = [str(row.get("zone_id")) for row in rows if row.get("zone_id")]
        relation_by_zone_id = {str(row.get("zone_id")): row for row in rows if row.get("zone_id")}

        if zone_ids:
            zones = self._request(
                "GET",
                "delivery_zones",
                bearer_token=bearer_token,
                params={
                    "select": "id,name,base_fee,estimated_minutes,is_active",
                    "id": f"in.({','.join(zone_ids)})",
                    "is_active": "eq.true",
                },
            ) or []
        else:
            zones = self._request(
                "GET",
                "delivery_zones",
                bearer_token=bearer_token,
                params={
                    "select": "id,name,base_fee,estimated_minutes,is_active",
                    "is_active": "eq.true",
                },
            ) or []

        overrides: dict[str, TariffZoneOverride] = {}
        for zone in zones:
            code = extract_zone_code(str(zone.get("name") or ""))
            if not code:
                continue
            relation = relation_by_zone_id.get(str(zone.get("id"))) or {}
            fee_override = relation.get("fee_override")
            base_fee = fee_override if fee_override is not None else zone.get("base_fee")
            try:
                estimated = int(zone["estimated_minutes"]) if zone.get("estimated_minutes") is not None else None
            except (TypeError, ValueError):
                estimated = None
            overrides[code] = TariffZoneOverride(
                zone_id=str(zone.get("id") or ""),
                code=code,
                label=str(zone.get("name") or f"Zona {code}"),
                base_fee=float(base_fee) if base_fee is not None else None,
                estimated_minutes=estimated,
            )
        return overrides

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
        fulfillment_type: str,
        items_snapshot: list[dict[str, Any]],
        taxable_base: float = 0.0,
        igv_rate: float = 0.18,
        igv_amount: float = 0.0,
        payment_processing_fee: float = 0.0,
        payment_processing_rate: float = 0.0,
        payment_processing_fixed: float = 0.0,
        payment_processing_provider: str = "culqi",
        payment_processing_note: str | None = None,
        payment_processing_tax_amount: float = 0.0,
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
            "taxable_base": taxable_base,
            "igv_rate": igv_rate,
            "igv_amount": igv_amount,
            "payment_processing_fee": payment_processing_fee,
            "payment_processing_rate": payment_processing_rate,
            "payment_processing_fixed": payment_processing_fixed,
            "payment_processing_provider": payment_processing_provider,
            "payment_processing_note": payment_processing_note,
            "payment_processing_tax_amount": payment_processing_tax_amount,
            "total": total,
            "distance_km": distance_km,
            "payment_method": payment_method,
            "fulfillment_type": fulfillment_type,
            "items_snapshot": items_snapshot,
            "status": "active",
            "expires_at": expires_at.isoformat(),
            "created_at": now.isoformat(),
        }
        try:
            rows = self._request(
                "POST",
                "order_quotes",
                bearer_token=bearer_token,
                json=payload,
                prefer="return=representation",
            )
        except SupabaseQuoteError as exc:
            detail = str(exc).lower()
            optional_columns = {
                "taxable_base",
                "igv_rate",
                "igv_amount",
                "payment_processing_fee",
                "payment_processing_rate",
                "payment_processing_fixed",
                "payment_processing_provider",
                "payment_processing_note",
                "payment_processing_tax_amount",
            }
            if "schema cache" not in detail and "column" not in detail:
                raise
            legacy_payload = {key: value for key, value in payload.items() if key not in optional_columns}
            rows = self._request(
                "POST",
                "order_quotes",
                bearer_token=bearer_token,
                json=legacy_payload,
                prefer="return=representation",
            )
        if rows:
            return {**payload, **rows[0]}
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


def calculate_road_distance_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Distancia vial usando un router compatible con OSRM /route/v1."""
    fallback_km = calculate_distance_km(lat1, lng1, lat2, lng2)
    routing_base_url = (settings.routing_api_url or "").rstrip("/")
    if not routing_base_url:
        return fallback_km

    url = f"{routing_base_url}/route/v1/driving/{lng1},{lat1};{lng2},{lat2}"
    try:
        response = requests.get(
            url,
            params={"overview": "false", "steps": "false"},
            timeout=max(1.0, float(settings.routing_timeout_seconds or 4.0)),
        )
        response.raise_for_status()
        data = response.json()
        route = (data.get("routes") or [None])[0]
        distance_m = route.get("distance") if isinstance(route, dict) else None
        if data.get("code") == "Ok" and distance_m is not None:
            return round(max(0.0, float(distance_m)) / 1000, 2)
    except Exception as exc:
        logger.warning("road_distance_fallback error=%s", exc)

    return fallback_km


def calculate_payment_processing_fee(amount: float, payment_method: str = "card") -> dict[str, Any]:
    """Comision de la pasarela que cobra este medio de pago, sobre todo el pedido.

    Tarjeta por Izipay: tasa + fijo en soles, ambos con IGV.
    Yape por Culqi: tasa con un minimo por pago, sin IGV (Culqi la publica
    inafecta). Tarjeta por Culqi: tasa + fijo
    en USD. La comision se sube de forma que, despues de que la pasarela
    descuente su parte, llegue completo el monto original.
    """
    if yape_propio_aplica(payment_method, int(round(max(0.0, amount) * 100))):
        # Yape directo al numero de ACME: no hay pasarela que cobre comision. Si
        # el monto exacto esta ocupado por otro pago en curso, el cliente yapea
        # hasta S/ 0.09 mas para distinguirlo; eso se cobra en el momento.
        return {
            "fee": 0.0,
            "rate": 0.0,
            "fixed": 0.0,
            "provider": "yape_propio",
            "note": "Yape directo a ACME: sin comision de pasarela.",
            "tax_amount": 0.0,
        }
    # Yape directo que no aplica (monto grande o pasarela apagada) se cotiza
    # como Yape normal por Culqi.
    method = "yape" if payment_method in ("yape", METODO_YAPE_DIRECTO) else "card"
    provider = payment_provider_for(method)
    igv = max(0.0, float(settings.igv_rate or 0.0))
    min_fee = 0.0
    fee_igv = igv
    if provider in ("izipay", "izipay_sdk"):
        rate = max(0.0, float(settings.izipay_card_rate or 0.0))
        fixed_pen = round(max(0.0, float(settings.izipay_card_fixed_pen or 0.0)), 2)
        label = "Izipay" + (" (Yape)" if method == "yape" else " (tarjeta)")
    elif method == "yape":
        rate = max(0.0, float(settings.culqi_yape_rate or 0.0))
        fixed_pen = 0.0
        min_fee = max(0.0, float(settings.culqi_yape_min_fee_pen or 0.0))
        if not settings.culqi_yape_apply_igv:
            fee_igv = 0.0
        label = "Culqi (Yape)"
    else:
        rate = max(0.0, float(settings.culqi_online_rate or 0.0))
        fixed_pen = round(max(0.0, float(settings.culqi_online_fixed_usd or 0.0)) * max(0.0, float(settings.culqi_exchange_rate or 0.0)), 2)
        label = "Culqi (tarjeta)"

    note = f"{label}: {rate * 100:.2f}%"
    if fixed_pen:
        note += f" + S/ {fixed_pen:.2f}"
    if fee_igv:
        note += " + IGV"
    if min_fee:
        note += f", minimo S/ {min_fee:.2f} por pago"
    note += "."

    if amount <= 0:
        return {"fee": 0.0, "rate": rate, "fixed": fixed_pen, "provider": provider, "note": note, "tax_amount": 0.0}

    charged = (amount + fixed_pen * (1 + fee_igv)) / max(0.0001, 1 - rate * (1 + fee_igv))
    fee = max(round(charged - amount, 2), round(min_fee, 2))
    tax_amount = round(fee - fee / (1 + fee_igv), 2)
    return {"fee": fee, "rate": rate, "fixed": fixed_pen, "provider": provider, "note": note, "tax_amount": tax_amount}


def calculate_included_igv(taxable_total: float) -> dict[str, float]:
    """Descompone un precio con IGV incluido en base imponible e impuesto."""
    rate = max(0.0, float(settings.igv_rate or 0.0))
    amount = max(0.0, float(taxable_total or 0.0))
    if rate <= 0:
        return {"taxable_base": round(amount, 2), "igv_amount": 0.0, "igv_rate": rate}
    taxable_base = round(amount / (1 + rate), 2)
    igv_amount = round(amount - taxable_base, 2)
    return {"taxable_base": taxable_base, "igv_amount": igv_amount, "igv_rate": rate}


def _extract_address_parts(data: dict[str, Any]) -> dict[str, str | None]:
    address = data.get("address") or {}
    road = (
        address.get("road")
        or address.get("pedestrian")
        or address.get("footway")
        or address.get("path")
        or address.get("neighbourhood")
        or address.get("suburb")
    )
    house_number = address.get("house_number")
    line1 = " ".join(str(value) for value in [road, house_number] if value).strip() or data.get("name")

    return {
        "line1": line1 or None,
        "district": (
            address.get("city_district")
            or address.get("suburb")
            or address.get("neighbourhood")
            or address.get("district")
        ),
        "city": address.get("city") or address.get("town") or address.get("village") or address.get("county"),
        "region": address.get("state") or address.get("region"),
        "country": address.get("country"),
        "display_name": data.get("display_name"),
    }


def reverse_geocode_point(lat: float, lng: float) -> dict[str, str | None]:
    """Obtiene direccion referencial para un punto usando Nominatim compatible."""
    response = requests.get(
        settings.geocoding_api_url,
        params={
            "format": "jsonv2",
            "lat": lat,
            "lon": lng,
            "accept-language": "es",
            "zoom": "18",
            "addressdetails": "1",
        },
        headers={
            "Accept": "application/json",
            "User-Agent": settings.geocoding_user_agent,
        },
        timeout=max(1.0, float(settings.geocoding_timeout_seconds or 5.0)),
    )
    response.raise_for_status()
    return _extract_address_parts(response.json())


def search_geocode_address(query: str, limit: int = 6) -> list[dict[str, Any]]:
    """Busca direcciones con sesgo a la ciudad de Huancavelica."""
    normalized = " ".join((query or "").strip().split())
    if not normalized:
        return []
    localized_query = normalized if "huancavelica" in normalized.lower() else f"{normalized}, Huancavelica, Peru"
    response = requests.get(
        settings.geocoding_search_api_url,
        params={
            "format": "jsonv2",
            "q": localized_query,
            "accept-language": "es",
            "addressdetails": "1",
            "countrycodes": "pe",
            "limit": max(1, min(limit, 10)),
            "viewbox": "-75.05,-12.73,-74.90,-12.86",
            "bounded": "0",
        },
        headers={
            "Accept": "application/json",
            "User-Agent": settings.geocoding_user_agent,
        },
        timeout=max(1.0, float(settings.geocoding_timeout_seconds or 5.0)),
    )
    response.raise_for_status()

    results: list[dict[str, Any]] = []
    seen: set[tuple[float, float, str]] = set()
    for item in response.json() or []:
        try:
            lat = round(float(item.get("lat")), 7)
            lng = round(float(item.get("lon")), 7)
        except (TypeError, ValueError):
            continue

        parts = _extract_address_parts(item)
        label = parts.get("line1") or item.get("name") or item.get("display_name") or normalized
        district = parts.get("district")
        if district and district not in str(label):
            label = f"{label} - {district}"
        key = (lat, lng, str(label))
        if key in seen:
            continue
        seen.add(key)
        results.append({
            "label": str(label),
            "lat": lat,
            "lng": lng,
            **parts,
        })
    return results


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
    """Tarifa de servicio sobre el subtotal neto de productos.

    12% si los productos suman hasta S/ 100; si pasan de S/ 100, 8% sobre todo
    el monto, sin tramo de 12% (S/ 150 -> S/ 12.00).
    """
    base = max(0.0, float(base_amount))
    threshold = float(settings.service_fee_tier_threshold_pen)
    if base > threshold:
        rate = float(settings.service_fee_rate_above_threshold)
    else:
        rate = float(settings.service_fee_rate_up_to_threshold)
    return round(base * rate, 2)


def _normalize_category_name(value: str | None) -> str:
    """Minusculas, sin tildes ni espacios sobrantes, para comparar nombres."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.strip().lower()


def service_fee_exempt_category_names() -> set[str]:
    """Nombres de categoria que no pagan tarifa de servicio (p. ej. bebidas)."""
    raw = str(settings.service_fee_exempt_categories or "")
    return {_normalize_category_name(part) for part in raw.split(",") if part.strip()}


def is_service_fee_exempt_category(category_name: str | None) -> bool:
    """True si la categoria esta exenta de la tarifa de servicio de ACME."""
    normalized = _normalize_category_name(category_name)
    if not normalized:
        return False
    return normalized in service_fee_exempt_category_names()


def service_fee_base_amount(total_base: float, exempt_subtotal: float) -> float:
    """Monto sobre el que se cobra la tarifa de servicio.

    Las categorias exentas (Bebidas ACME) no suman a la tarifa cuando acompañan
    a otros productos. Pero si el pedido es SOLO de esas categorias, si pagan la
    tarifa sobre todo el monto: ese cobro cubre el mantenimiento de ACME
    (combustible, filtros, electricidad, pasajes).
    """
    total = max(0.0, float(total_base))
    exempt = max(0.0, float(exempt_subtotal))
    non_exempt = round(total - exempt, 2)
    return non_exempt if non_exempt > 0 else total


def multi_store_surcharge_rates() -> list[float]:
    rates: list[float] = []
    for part in str(settings.multi_store_delivery_surcharges or "").split(","):
        part = part.strip()
        if part:
            rates.append(max(0.0, float(part)))
    return rates


def calculate_multi_store_surcharge(base_delivery_fee: float, branch_count: int) -> tuple[float, float]:
    """Recargo al envio por los locales extra de un pedido: (monto, tasa acumulada).

    Cada local extra suma su porcentaje del envio base (10%, 15%, 20%, 25%).
    Lanza ValueError si el pedido trae mas locales de los permitidos.
    """
    extra = max(0, int(branch_count) - 1)
    rates = multi_store_surcharge_rates()
    if extra > len(rates):
        raise ValueError(f"Un pedido puede juntar hasta {len(rates) + 1} locales.")
    rate = round(sum(rates[:extra]), 4)
    return round(max(0.0, float(base_delivery_fee)) * rate, 2), rate


def effective_service_fee_rate(base_amount: float, service_fee: float) -> float:
    """Porcentaje efectivo cobrado (tarifa / base), para guardar en la orden.

    La columna es numeric(5,4), asi que se limita a 9.9999 por si un pedido
    casi sin productos lleva una comision de tarjeta mayor que su base.
    Si no hay tarifa (base 0 por ser todo bebidas, o tarifa 0), el efectivo es 0%.
    """
    if base_amount <= 0 or service_fee <= 0:
        return 0.0
    return min(9.9999, round(service_fee / base_amount, 4))
