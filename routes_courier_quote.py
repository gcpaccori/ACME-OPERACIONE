from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query

from models import CourierGeocodeSearchResult, CourierQuoteBranch, CourierQuoteGroup, CourierQuoteRequest, CourierQuoteResponse, CourierReverseGeocodeResponse
from services_courier_quote import (
    SupabaseQuoteError,
    SupabaseQuoteService,
    calculate_road_distance_km,
    reverse_geocode_point,
    search_geocode_address,
)
from services_courier_tariffs import calculate_courier_tariff, detect_huancavelica_coverage
from services_pricing import (
    compute_order_pricing,
    exempt_category_names,
    load_pricing_config_with_version,
    multi_store_surcharge,
    normalize_category_name,
    pricing_breakdown,
    tariff_config,
)
from services_supabase_auth import SupabaseAuthError, verify_supabase_user

router = APIRouter(prefix="/api/courier", tags=["courier-quote"])
logger = logging.getLogger(__name__)

_FALLBACK_DISTANCE_KM = 1.0  # Distancia minima por defecto cuando no hay coords


def _extract_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token.strip()


@router.post("/quote", response_model=CourierQuoteResponse)
def create_courier_quote(
    payload: CourierQuoteRequest,
    authorization: str | None = Header(default=None),
):
    """
    Generar una cotizacion de precio para un pedido courier.
    Calcula subtotal real desde Supabase, tarifa de servicio, delivery fee y total.
    Guarda la cotizacion en order_quotes con expiracion de 5 minutos.
    """
    token = _extract_bearer(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Se requiere Bearer token para cotizar.")

    try:
        customer_id = verify_supabase_user(token)
    except SupabaseAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    supabase = SupabaseQuoteService()
    # Configuracion de cobros del admin (cache de 30 s; por defecto si no hay).
    pricing, pricing_version = load_pricing_config_with_version()
    tariff_cfg = tariff_config(pricing)

    try:
        # 1. Locales del pedido: el del pedido primero y luego los de los
        # productos que vienen de otros locales.
        branch_ids: list[str] = [payload.branch_id]
        for item in payload.items:
            item_branch = item.branch_id or payload.branch_id
            if item_branch not in branch_ids:
                branch_ids.append(item_branch)
        if not any((item.branch_id or payload.branch_id) == payload.branch_id for item in payload.items):
            raise HTTPException(status_code=422, detail="El local del pedido no tiene productos en el carrito.")
        try:
            multi_store_surcharge(pricing, 0, len(branch_ids))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        # 2. Cobertura operativa urbana de Huancavelica (depende del cliente)
        client_lat = payload.latitude
        client_lng = payload.longitude
        has_client_coords = (
            client_lat is not None
            and client_lng is not None
            and not (client_lat == 0.0 and client_lng == 0.0)
        )
        coverage = detect_huancavelica_coverage(client_lat, client_lng, payload.fulfillment_type, config=tariff_cfg)
        manual_out_of_city = payload.is_out_of_city or (payload.zone or "").strip().upper() == "D"
        effective_is_out_of_city = bool(manual_out_of_city or coverage.get("is_out_of_city"))
        if manual_out_of_city and coverage.get("status") != "outside":
            coverage = {
                **coverage,
                "status": "outside",
                "label": "Marcado fuera de cobertura urbana",
                "detail": "El pedido fue marcado para tarifa fuera de ciudad; se cotiza con Zona D por kilometraje.",
                "is_out_of_city": True,
            }

        # 3. Distancia y tarifa courier de cada local hasta el cliente. El envio
        # base es el del local mas caro de llevar; los demas suman su recargo.
        branches: dict[str, dict[str, Any]] = {}
        branch_quotes: dict[str, tuple[float, dict[str, Any]]] = {}
        for branch_id in branch_ids:
            branch = supabase.get_branch(branch_id, token)
            branches[branch_id] = branch
            branch_lat = branch.get("lat")
            branch_lng = branch.get("lng")
            has_branch_coords = (
                branch_lat is not None
                and branch_lng is not None
                and not (branch_lat == 0.0 and branch_lng == 0.0)
            )
            if has_client_coords and has_branch_coords:
                branch_distance = calculate_road_distance_km(
                    float(client_lat), float(client_lng),
                    float(branch_lat), float(branch_lng),
                )
            else:
                if not has_branch_coords:
                    logger.warning(
                        "quote_missing_branch_coords branch_id=%s, usando distancia fallback=%.1f km",
                        branch_id, _FALLBACK_DISTANCE_KM,
                    )
                else:
                    logger.warning(
                        "quote_missing_client_coords customer_id=%s, usando distancia fallback=%.1f km",
                        customer_id, _FALLBACK_DISTANCE_KM,
                    )
                branch_distance = _FALLBACK_DISTANCE_KM

            # 4. Tarifa courier por zonas urbanas de Huancavelica
            zone_overrides = supabase.get_delivery_zone_overrides(branch_id, token)
            branch_tariff = calculate_courier_tariff(
                distance_km=branch_distance,
                zone=payload.zone,
                weight_kg=payload.weight_kg,
                service_type=payload.service_type,
                is_difficult_zone=payload.is_difficult_zone,
                is_out_of_city=effective_is_out_of_city,
                wait_or_second_visit=payload.wait_or_second_visit,
                fulfillment_type=payload.fulfillment_type,
                zone_overrides=zone_overrides,
                destination_lat=float(client_lat) if has_client_coords else None,
                destination_lng=float(client_lng) if has_client_coords else None,
                origin_lat=float(branch_lat) if has_branch_coords else None,
                origin_lng=float(branch_lng) if has_branch_coords else None,
                config=tariff_cfg,
            )
            branch_quotes[branch_id] = (branch_distance, branch_tariff)

        base_branch_id = max(branch_ids, key=lambda bid: float(branch_quotes[bid][1]["tarifa_final"]))
        distance_km, tariff = branch_quotes[base_branch_id]
        base_delivery_fee = float(tariff["tarifa_final"])

        # 5. Obtener precios reales de productos desde Supabase
        all_product_ids = list({item.product_id for item in payload.items})
        products_rows = supabase.get_products_with_prices(all_product_ids, token)
        price_map: dict[str, float] = {str(row["id"]): float(row["base_price"]) for row in products_rows}
        name_map: dict[str, str] = {str(row["id"]): str(row.get("name", "")) for row in products_rows}
        merchant_map: dict[str, str] = {str(row["id"]): str(row.get("merchant_id") or "") for row in products_rows}
        category_map: dict[str, str] = {str(row["id"]): str(row.get("category_id") or "") for row in products_rows}

        # Categorias exentas de la tarifa (p. ej. Bebidas ACME), segun la configuracion.
        exempt_names = exempt_category_names(pricing)
        category_rows = supabase.get_categories(list(category_map.values()), token) if exempt_names else []
        exempt_category_ids: set[str] = {
            str(row["id"]) for row in category_rows if normalize_category_name(row.get("name")) in exempt_names
        }

        # 6. Obtener deltas de modificadores
        all_modifier_ids: list[str] = []
        for item in payload.items:
            all_modifier_ids.extend(item.modifier_ids)
        all_modifier_ids = list(set(all_modifier_ids))

        modifier_map: dict[str, float] = {}
        if all_modifier_ids:
            mod_rows = supabase.get_modifiers_prices(all_modifier_ids, token)
            modifier_map = {str(row["id"]): float(row.get("price_delta", 0)) for row in mod_rows}

        # 7. Calcular subtotal
        branch_subtotals: dict[str, float] = {}
        items_snapshot: list[dict[str, Any]] = []
        pricing_lines: list[dict[str, Any]] = []
        for item in payload.items:
            pid = item.product_id
            if pid not in price_map:
                raise HTTPException(
                    status_code=422,
                    detail=f"Producto '{pid}' no encontrado o sin precio en Supabase.",
                )
            item_branch = item.branch_id or payload.branch_id
            branch_merchant = str(branches[item_branch].get("merchant_id") or "")
            if merchant_map.get(pid) and branch_merchant and merchant_map[pid] != branch_merchant:
                raise HTTPException(
                    status_code=422,
                    detail=f"El producto '{name_map.get(pid) or pid}' no pertenece a ese local.",
                )
            unit_price = price_map[pid]
            mod_total = sum(modifier_map.get(mid, 0.0) for mid in item.modifier_ids)
            line_total = (unit_price + mod_total) * item.quantity
            is_fee_exempt = category_map.get(pid, "") in exempt_category_ids
            branch_subtotals[item_branch] = branch_subtotals.get(item_branch, 0.0) + line_total
            pricing_lines.append({
                "line_total": line_total,
                "quantity": item.quantity,
                "merchant_id": merchant_map.get(pid) or branch_merchant,
                "exempt": is_fee_exempt,
            })
            items_snapshot.append({
                "branch_id": item_branch,
                "product_id": pid,
                "product_name": name_map.get(pid, ""),
                "unit_price": unit_price,
                "modifier_ids": item.modifier_ids,
                "modifier_delta": mod_total,
                "quantity": item.quantity,
                "line_total": round(line_total, 2),
                "service_fee_exempt": is_fee_exempt,
            })

        # 8. Descuento (por ahora siempre 0)
        discount = 0.0

        # 9-13. Tarifa, condicion en productos, recargo por locales, IGV y
        # comision de pago, todo con la configuracion de cobros del admin.
        try:
            priced = compute_order_pricing(
                pricing,
                lines=pricing_lines,
                delivery_fee=base_delivery_fee,
                branch_count=len(branch_ids),
                tip=float(payload.tip_amount),
                payment_method=payload.payment_method,
                fulfillment_type=payload.fulfillment_type,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        subtotal = priced["products"]
        multi_store_surcharge_amount = priced["multi_store_surcharge"]
        multi_store_rate = priced["multi_store_rate"]
        delivery_fee = round(priced["delivery_fee"] + multi_store_surcharge_amount, 2)
        # La condicion en productos es de ACME: en los campos de siempre viaja
        # dentro de service_fee (subtotal sigue siendo lo que recibe el local).
        service_fee = round(priced["service_fee"] + priced["product_markup"], 2)
        service_fee_base = priced["service_fee_base"]
        service_fee_rate = min(9.9999, round(priced["service_fee_rate"], 4)) if priced["service_fee"] > 0 else 0.0
        tip_amount = priced["tip"]
        tax_breakdown = {
            "taxable_base": priced["taxable_base"],
            "igv_rate": priced["igv_rate"],
            "igv_amount": priced["igv_amount"],
        }
        igv_added = bool(priced["igv_added"])
        processing = priced["gateway"]
        payment_processing_fee = float(priced["gateway_fee"])
        payment_processing_note = str(processing.get("note") or "")
        total = priced["total"]

        # 13a. Yape directo: el APK de la app cliente ya instalado solo ofrece
        # Yape directo si la comision de pago viene en cero, asi que la de BCP
        # viaja dentro de service_fee y aparte en yape_directo_fee; la web la
        # resta de la tarifa y la muestra como comision de pago. El total no cambia.
        yape_directo_fee = 0.0
        if processing.get("provider") == "yape_propio" and payment_processing_fee > 0:
            yape_directo_fee = payment_processing_fee
            service_fee = round(service_fee + yape_directo_fee, 2)
            payment_processing_fee = 0.0

        # 14. Guardar cotizacion
        quote = supabase.save_quote(
            customer_id=customer_id,
            branch_id=payload.branch_id,
            subtotal=subtotal,
            discount=discount,
            service_fee=service_fee,
            service_fee_rate=service_fee_rate,
            delivery_fee=delivery_fee,
            tip_amount=tip_amount,
            total=total,
            distance_km=distance_km,
            payment_method=payload.payment_method,
            fulfillment_type=payload.fulfillment_type,
            items_snapshot=items_snapshot,
            taxable_base=float(tax_breakdown["taxable_base"]),
            igv_rate=float(tax_breakdown["igv_rate"]),
            igv_amount=float(tax_breakdown["igv_amount"]),
            payment_processing_fee=payment_processing_fee,
            payment_processing_rate=float(processing["rate"]),
            payment_processing_fixed=float(processing["fixed"]),
            payment_processing_provider=str(processing["provider"]),
            payment_processing_note=payment_processing_note,
            payment_processing_tax_amount=float(processing["tax_amount"]),
            pricing_version=pricing_version,
            pricing_breakdown=pricing_breakdown(priced, pricing_version),
            bearer_token=token,
        )

        logger.info(
            "courier_quote_ok customer=%s branch=%s subtotal=%.2f total=%.2f dist=%.2fkm",
            customer_id, payload.branch_id, subtotal, total, distance_km,
        )

        return CourierQuoteResponse(
            quote_id=str(quote["id"]),
            subtotal=subtotal,
            discount=discount,
            service_fee=service_fee,
            service_fee_rate=service_fee_rate,
            service_fee_base=service_fee_base,
            delivery_fee=delivery_fee,
            tip_amount=tip_amount,
            taxable_base=float(tax_breakdown["taxable_base"]),
            igv_rate=float(tax_breakdown["igv_rate"]),
            igv_amount=float(tax_breakdown["igv_amount"]),
            igv_added=igv_added,
            payment_processing_fee=payment_processing_fee,
            yape_directo_fee=yape_directo_fee,
            payment_processing_rate=float(processing["rate"]),
            payment_processing_fixed=float(processing["fixed"]),
            payment_processing_provider=str(processing["provider"]),
            payment_processing_note=payment_processing_note,
            payment_processing_tax_amount=float(processing["tax_amount"]),
            total=total,
            distance_km=distance_km,
            coverage_status=str(coverage.get("status") or "unknown"),
            coverage_label=str(coverage.get("label") or ""),
            coverage_detail=str(coverage.get("detail") or ""),
            is_out_of_city=effective_is_out_of_city,
            delivery_zone=str(tariff.get("zona_codigo") or ""),
            delivery_zone_label=str(tariff.get("zona_aplicada") or ""),
            delivery_detail=str(tariff.get("detalle_calculo") or ""),
            delivery_surcharges_total=float(tariff.get("recargos_total") or 0),
            delivery_surcharges=tariff.get("recargos") or [],
            branch_count=len(branch_ids),
            multi_store_surcharge=multi_store_surcharge_amount,
            multi_store_surcharge_rate=multi_store_rate,
            branches=[
                CourierQuoteBranch(
                    branch_id=bid,
                    subtotal=round(branch_subtotals.get(bid, 0.0), 2),
                    distance_km=branch_quotes[bid][0],
                )
                for bid in branch_ids
            ],
            product_markup=priced["product_markup"],
            client_groups=[CourierQuoteGroup(**g) for g in priced["client_groups"]],
            expires_at=str(quote.get("expires_at", "")),
        )

    except HTTPException:
        raise
    except SupabaseQuoteError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("courier_quote_unexpected_error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error interno al generar cotizacion: {exc}") from exc


@router.get("/reverse-geocode", response_model=CourierReverseGeocodeResponse)
def reverse_geocode(
    lat: float = Query(..., ge=-90, le=90),
    lng: float = Query(..., ge=-180, le=180),
):
    """Direccion referencial desde coordenadas seleccionadas en mapa."""
    try:
        return CourierReverseGeocodeResponse(**reverse_geocode_point(lat, lng))
    except Exception as exc:
        logger.warning("reverse_geocode_failed lat=%s lng=%s error=%s", lat, lng, exc)
        raise HTTPException(status_code=502, detail="No se pudo obtener direccion del mapa.") from exc


@router.get("/geocode-search", response_model=list[CourierGeocodeSearchResult])
def geocode_search(
    q: str = Query(..., min_length=3, max_length=160),
    limit: int = Query(default=6, ge=1, le=10),
):
    """Busca direcciones candidatas dentro de Huancavelica."""
    try:
        return [CourierGeocodeSearchResult(**item) for item in search_geocode_address(q, limit)]
    except Exception as exc:
        logger.warning("geocode_search_failed q=%s error=%s", q, exc)
        raise HTTPException(status_code=502, detail="No se pudo buscar la direccion.") from exc
