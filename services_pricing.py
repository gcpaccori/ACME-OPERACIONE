"""Configuracion de cobros que el dueño de ACME cambia desde el admin.

Todo lo que ACME decide cobrar vive en un solo documento JSON guardado en
Supabase (tabla public.pricing_config, fila "global"): tarifa de servicio y
sus tramos, condicion en el precio de los productos (general y por negocio),
envio por zonas y por puntos del mapa, recargo por varios locales, IGV y lo que
se le cobra al cliente por cada pasarela.

Lo que las pasarelas le cobran a ACME (BCP, Izipay, Culqi) NO esta aqui: eso
lo fija el contrato y sigue en las variables de entorno (config.py). El admin
lo ve, pero no lo edita.

Si la tabla no existe o Supabase no responde, se cobra con los valores por
defecto, que son los mismos que se cobraban antes de esta configuracion.

compute_order_pricing() es el unico calculo de precios: lo usan la cotizacion
del carrito y el simulador del admin, asi que lo que el dueño simula es
exactamente lo que paga el cliente.
"""
from __future__ import annotations

import copy
import logging
import math
import threading
import time
import unicodedata
from typing import Any

import requests

from config import settings

logger = logging.getLogger(__name__)

TABLE = "pricing_config"
HISTORY_TABLE = "pricing_config_history"
ROW_ID = "global"
CACHE_SECONDS = 30.0

GROUPS = ("producto", "envio", "comision")
SERVICE_FEE_BASES = ("products", "delivery", "multi_store")
TIER_BY = ("products", "base")
TIER_MODES = ("whole", "progressive")
IGV_MODES = ("added", "included", "none")
GATEWAY_MODES = ("cost", "percent", "absorb")
GATEWAYS = ("yape_directo", "izipay", "culqi_yape", "culqi_card")
ROUNDING_MODES = ("nearest", "up", "down")


class PricingConfigError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Valores por defecto: lo que se cobraba antes de la configuracion
# ---------------------------------------------------------------------------

def _csv_floats(raw: str) -> list[float]:
    out: list[float] = []
    for part in str(raw or "").split(","):
        part = part.strip()
        if part:
            out.append(max(0.0, float(part)))
    return out


def _tariff_json() -> dict[str, Any]:
    from services_courier_tariffs import load_tariff_config

    return load_tariff_config()


DELIVERY_KEYS = (
    "rounding_increment",
    "rounding_mode",
    "service_surcharges",
    "weight",
    "difficult_zone_fee",
    "wait_or_second_visit_fee",
    "outside_city",
    "zones",
    "sector_pricing",
)


def default_config() -> dict[str, Any]:
    """Configuracion por defecto, armada en cada llamada desde config.py y el JSON de tarifas."""
    tariff = _tariff_json()
    threshold = float(settings.service_fee_tier_threshold_pen)
    exempt = [p.strip() for p in str(settings.service_fee_exempt_categories or "").split(",") if p.strip()]
    delivery = {key: copy.deepcopy(tariff.get(key)) for key in DELIVERY_KEYS if key in tariff}
    delivery["group"] = "envio"
    return {
        "version": 1,
        "service_fee": {
            "enabled": True,
            # Sobre que se calcula: productos, envio y/o recargo por locales.
            "base": ["products"],
            # Que monto elige el tramo: los productos o la misma base.
            "tier_by": "base",
            # whole: el porcentaje del tramo va sobre todo el monto (S/ 101 -> 8% de 101).
            # progressive: cada parte paga su tramo (12% de 100 + 8% del resto).
            "tier_mode": "whole",
            "tiers": [
                {"up_to": threshold, "rate": float(settings.service_fee_rate_up_to_threshold)},
                {"up_to": None, "rate": float(settings.service_fee_rate_above_threshold)},
            ],
            "fixed": 0.0,
            "min": 0.0,
            "max": 0.0,
            "exempt_categories": exempt,
            # Un pedido SOLO de categorias exentas si paga tarifa (mantenimiento).
            "exempt_only_orders_pay": True,
            "group": "comision",
        },
        # Condicion en el precio de los productos (ganancia de ACME escondida en
        # el producto). El local recibe su precio; la diferencia es de ACME.
        "product_markup": {
            "rate": 0.0,
            "fixed_per_unit": 0.0,
            "group": "producto",
        },
        # Reglas por negocio: condicion propia, tarifa propia o sin tarifa.
        "merchant_rules": [],
        "multi_store": {
            # El 2do local suma 10% del envio, el 3ro 15%, ... (acumulado).
            "rates": _csv_floats(settings.multi_store_delivery_surcharges),
            # Tope en soles del recargo; 0 = sin tope.
            "max_amount": 0.0,
            "group": "envio",
        },
        "delivery": delivery,
        "igv": {
            "rate": float(settings.igv_rate),
            # added: se suma encima; included: va dentro del precio; none: no se cobra.
            "mode": "added",
            "applies_to": {
                "delivery": True,
                "multi_store": True,
                "service_fee": True,
                "product_markup": True,
            },
        },
        # Lo que se le cobra al cliente por cada pasarela.
        # cost: exactamente lo que cobra la pasarela (ACME recibe todo completo).
        # percent: un porcentaje propio del total final (+ fijo), como el 3.5% de Yape directo.
        # absorb: ACME lo paga y el cliente no ve comision.
        "gateways": {
            "group": "comision",
            "yape_directo": {"mode": "percent", "rate": float(settings.yape_directo_fee_rate), "fixed": 0.0},
            "izipay": {"mode": "cost", "rate": 0.0, "fixed": 0.0},
            "culqi_yape": {"mode": "cost", "rate": 0.0, "fixed": 0.0},
            "culqi_card": {"mode": "cost", "rate": 0.0, "fixed": 0.0},
        },
        "display": {
            "labels": {"producto": "Productos", "envio": "Envío", "comision": "Comisión"},
        },
    }


def gateway_costs() -> dict[str, Any]:
    """Lo que cada pasarela le cobra a ACME (contrato, variables de entorno). Solo lectura."""
    fx = float(settings.culqi_exchange_rate or 0.0)
    return {
        "igv_rate": float(settings.igv_rate),
        "yape_directo": {
            "label": "Yape directo (BCP Yape empresa)",
            "rate": float(settings.yape_directo_cost_rate),
            "fixed": 0.0,
            "igv": False,
            "note": "BCP descuenta este porcentaje de cada yapeo que entra.",
        },
        "izipay": {
            "label": "Tarjeta (Izipay)",
            "rate": float(settings.izipay_card_rate),
            "fixed": float(settings.izipay_card_fixed_pen),
            "igv": True,
            "note": "Tasa + fijo por operación, ambos más IGV.",
        },
        "culqi_yape": {
            "label": "Yape por Culqi",
            "rate": float(settings.culqi_yape_rate),
            "fixed": round(float(settings.culqi_yape_fixed_usd) * fx, 2),
            "fixed_usd": float(settings.culqi_yape_fixed_usd),
            "min_fee": float(settings.culqi_yape_min_fee_pen),
            "min_fee_below": float(settings.culqi_yape_min_fee_below_pen),
            "igv": bool(settings.culqi_yape_apply_igv),
            "note": "Mínimo por pago bajo cierto monto (IGV incluido); desde ahí tasa + fijo en dólares, más IGV.",
        },
        "culqi_card": {
            "label": "Tarjeta (Culqi)",
            "rate": float(settings.culqi_online_rate),
            "fixed": round(float(settings.culqi_online_fixed_usd) * fx, 2),
            "fixed_usd": float(settings.culqi_online_fixed_usd),
            "igv": True,
            "note": "Tasa + fijo en dólares, más IGV.",
        },
        "exchange_rate": fx,
    }


# ---------------------------------------------------------------------------
# Lectura y guardado en Supabase
# ---------------------------------------------------------------------------

_cache_lock = threading.Lock()
_cache: dict[str, Any] = {"at": 0.0, "row": None}


def _sb_headers(prefer: str | None = None) -> dict[str, str]:
    key = settings.supabase_service_role_key or ""
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


def _sb_url(table: str) -> str:
    return f"{settings.supabase_url.rstrip('/')}/rest/v1/{table}"


def fetch_stored_row(force: bool = False) -> dict[str, Any] | None:
    """Fila guardada (config, updated_at, updated_by) o None. Cache de 30 s."""
    if not settings.supabase_url or not settings.supabase_service_role_key:
        return None
    now = time.monotonic()
    with _cache_lock:
        if not force and now - float(_cache["at"]) < CACHE_SECONDS:
            return copy.deepcopy(_cache["row"])
    row: dict[str, Any] | None = None
    try:
        resp = requests.get(
            _sb_url(TABLE),
            headers=_sb_headers(),
            params={"select": "config,updated_at,updated_by", "id": f"eq.{ROW_ID}", "limit": "1"},
            timeout=6,
        )
        if resp.status_code < 400:
            rows = resp.json() or []
            row = rows[0] if rows else None
        else:
            # Tabla aun no creada (falta correr la migracion): se cobra por defecto.
            logger.warning("pricing_config_unavailable status=%s body=%s", resp.status_code, resp.text[:200])
    except Exception as exc:  # pragma: no cover - red
        logger.warning("pricing_config_fetch_failed error=%s", exc)
        with _cache_lock:
            # Sin red se sigue con lo ultimo que se leyo.
            return copy.deepcopy(_cache["row"])
    with _cache_lock:
        _cache["at"] = now
        _cache["row"] = row
    return copy.deepcopy(row)


def clear_cache() -> None:
    with _cache_lock:
        _cache["at"] = 0.0
        _cache["row"] = None


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def merge_config(override: dict[str, Any] | None) -> dict[str, Any]:
    return _deep_merge(default_config(), override or {})


def load_pricing_config() -> dict[str, Any]:
    """Configuracion vigente: la guardada por el admin encima de los valores por defecto."""
    row = fetch_stored_row()
    stored = (row or {}).get("config") if isinstance(row, dict) else None
    if not isinstance(stored, dict):
        return default_config()
    try:
        return validate_config(merge_config(stored))
    except PricingConfigError as exc:
        logger.error("pricing_config_invalid_using_defaults error=%s", exc)
        return default_config()


def save_pricing_config(config: dict[str, Any], user_id: str) -> dict[str, Any]:
    clean = validate_config(merge_config(config))
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    payload = {"id": ROW_ID, "config": clean, "updated_at": now, "updated_by": user_id}
    resp = requests.post(
        _sb_url(TABLE),
        headers=_sb_headers("resolution=merge-duplicates,return=representation"),
        params={"on_conflict": "id"},
        json=payload,
        timeout=10,
    )
    if resp.status_code >= 400:
        raise RuntimeError(
            "No se pudo guardar la configuracion de cobros. Si la tabla pricing_config no existe, "
            f"falta correr la migracion. Detalle: {resp.text[:300]}"
        )
    try:
        requests.post(
            _sb_url(HISTORY_TABLE),
            headers=_sb_headers("return=minimal"),
            json={"config": clean, "updated_by": user_id},
            timeout=6,
        )
    except Exception as exc:  # pragma: no cover - el historial no frena el guardado
        logger.warning("pricing_history_failed error=%s", exc)
    clear_cache()
    return {"config": clean, "updated_at": now, "updated_by": user_id}


def fetch_history(limit: int = 20) -> list[dict[str, Any]]:
    if not settings.supabase_url or not settings.supabase_service_role_key:
        return []
    resp = requests.get(
        _sb_url(HISTORY_TABLE),
        headers=_sb_headers(),
        params={"select": "id,created_at,updated_by,config", "order": "created_at.desc", "limit": str(limit)},
        timeout=8,
    )
    if resp.status_code >= 400:
        return []
    return resp.json() or []


# ---------------------------------------------------------------------------
# Validacion
# ---------------------------------------------------------------------------

def _num(value: Any, field: str, *, lo: float = 0.0, hi: float | None = None, allow_none: bool = False) -> float | None:
    if value is None or value == "":
        if allow_none:
            return None
        raise PricingConfigError(f"Falta un numero en {field}.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise PricingConfigError(f"{field} debe ser un numero.") from exc
    if not math.isfinite(number) or number < lo or (hi is not None and number > hi):
        rango = f"entre {lo:g} y {hi:g}" if hi is not None else f"mayor o igual a {lo:g}"
        raise PricingConfigError(f"{field} debe estar {rango}.")
    return number


def _choice(value: Any, options: tuple[str, ...], field: str) -> str:
    text = str(value or "").strip()
    if text not in options:
        raise PricingConfigError(f"{field} debe ser uno de: {', '.join(options)}.")
    return text


def validate_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Revisa y normaliza la configuracion. Lanza PricingConfigError con un mensaje claro."""
    c = copy.deepcopy(cfg)

    sf = c["service_fee"]
    sf["enabled"] = bool(sf.get("enabled", True))
    base = [b for b in (sf.get("base") or []) if b in SERVICE_FEE_BASES]
    if sf["enabled"] and not base:
        raise PricingConfigError("La tarifa de servicio necesita al menos una base (productos, envio o recargo).")
    sf["base"] = list(dict.fromkeys(base))
    sf["tier_by"] = _choice(sf.get("tier_by"), TIER_BY, "Tramo segun")
    sf["tier_mode"] = _choice(sf.get("tier_mode"), TIER_MODES, "Modo de tramos")
    tiers = sf.get("tiers") or []
    if not tiers:
        raise PricingConfigError("La tarifa de servicio necesita al menos un tramo.")
    clean_tiers: list[dict[str, Any]] = []
    last = -1.0
    for i, tier in enumerate(tiers):
        is_last = i == len(tiers) - 1
        up_to = None if is_last else _num(tier.get("up_to"), f"Tramo {i + 1}: hasta")
        if up_to is not None:
            if up_to <= last:
                raise PricingConfigError("Los tramos deben ir de menor a mayor monto.")
            last = up_to
        clean_tiers.append({"up_to": up_to, "rate": _num(tier.get("rate"), f"Tramo {i + 1}: porcentaje", hi=1.0)})
    sf["tiers"] = clean_tiers
    sf["fixed"] = _num(sf.get("fixed", 0), "Tarifa fija")
    sf["min"] = _num(sf.get("min", 0), "Tarifa minima")
    sf["max"] = _num(sf.get("max", 0), "Tarifa maxima")
    if sf["max"] and sf["max"] < sf["min"]:
        raise PricingConfigError("La tarifa maxima no puede ser menor que la minima.")
    sf["exempt_categories"] = [str(x).strip() for x in (sf.get("exempt_categories") or []) if str(x).strip()]
    sf["exempt_only_orders_pay"] = bool(sf.get("exempt_only_orders_pay", True))
    sf["group"] = _choice(sf.get("group"), GROUPS, "Grupo de la tarifa")

    pm = c["product_markup"]
    pm["rate"] = _num(pm.get("rate", 0), "Condicion en productos (%)", hi=5.0)
    pm["fixed_per_unit"] = _num(pm.get("fixed_per_unit", 0), "Condicion en productos (S/ por unidad)")
    pm["group"] = _choice(pm.get("group"), GROUPS, "Grupo de la condicion en productos")

    rules: list[dict[str, Any]] = []
    seen: set[str] = set()
    for rule in c.get("merchant_rules") or []:
        mid = str(rule.get("merchant_id") or "").strip()
        if not mid or mid in seen:
            continue
        seen.add(mid)
        rules.append({
            "merchant_id": mid,
            "name": str(rule.get("name") or "")[:120],
            "markup_rate": _num(rule.get("markup_rate"), "Condicion del negocio (%)", hi=5.0, allow_none=True),
            "markup_fixed_per_unit": _num(rule.get("markup_fixed_per_unit"), "Condicion del negocio (S/)", allow_none=True),
            "service_fee_rate": _num(rule.get("service_fee_rate"), "Tarifa propia del negocio", hi=1.0, allow_none=True),
            "service_fee_exempt": bool(rule.get("service_fee_exempt", False)),
        })
    c["merchant_rules"] = rules

    ms = c["multi_store"]
    ms["rates"] = [_num(r, "Recargo por local extra", hi=5.0) for r in (ms.get("rates") or [])]
    ms["max_amount"] = _num(ms.get("max_amount", 0), "Tope del recargo por locales")
    ms["group"] = _choice(ms.get("group"), GROUPS, "Grupo del recargo por locales")

    d = c["delivery"]
    d["group"] = _choice(d.get("group"), GROUPS, "Grupo del envio")
    d["rounding_increment"] = _num(d.get("rounding_increment", 0.5), "Redondeo del envio")
    d["rounding_mode"] = _choice(d.get("rounding_mode") or "nearest", ROUNDING_MODES, "Modo de redondeo")
    for z in d.get("zones") or []:
        z["base_fee"] = _num(z.get("base_fee"), f"Zona {z.get('code')}: precio")
        if z.get("max_distance_km") is not None:
            z["max_distance_km"] = _num(z.get("max_distance_km"), f"Zona {z.get('code')}: km")
    oc = d.get("outside_city") or {}
    oc["base_fee"] = _num(oc.get("base_fee", 0), "Fuera de ciudad: base")
    oc["cost_per_km"] = _num(oc.get("cost_per_km", 0), "Fuera de ciudad: por km")
    d["outside_city"] = oc
    sp = d.get("sector_pricing") or {}
    sp["enabled"] = bool(sp.get("enabled", False))
    sp["center_fee"] = _num(sp.get("center_fee", 7), "Precio del centro")
    points = []
    for i, p in enumerate(sp.get("points") or []):
        lat = _num(p.get("lat"), f"Punto {i + 1}: latitud", lo=-90, hi=90)
        lng = _num(p.get("lng"), f"Punto {i + 1}: longitud", lo=-180, hi=180)
        points.append({
            **{k: v for k, v in p.items() if k not in ("lat", "lng", "fee", "sector")},
            "lat": lat,
            "lng": lng,
            "fee": _num(p.get("fee"), f"Punto {i + 1}: precio"),
            "sector": str(p.get("sector") or "Sector")[:80],
        })
    sp["points"] = points
    d["sector_pricing"] = sp
    surch = d.get("service_surcharges") or {}
    d["service_surcharges"] = {k: _num(v, f"Recargo {k}", hi=5.0) for k, v in surch.items()}
    w = d.get("weight") or {}
    d["weight"] = {
        "included_kg": _num(w.get("included_kg", 2), "Peso incluido"),
        "step_kg": _num(w.get("step_kg", 1), "Peso por paso", lo=0.01),
        "fee_per_step": _num(w.get("fee_per_step", 0), "Precio por paso de peso"),
    }
    d["difficult_zone_fee"] = _num(d.get("difficult_zone_fee", 0), "Zona dificil")
    d["wait_or_second_visit_fee"] = _num(d.get("wait_or_second_visit_fee", 0), "Espera o segunda visita")

    igv = c["igv"]
    igv["rate"] = _num(igv.get("rate", 0.18), "Tasa de IGV", hi=1.0)
    igv["mode"] = _choice(igv.get("mode"), IGV_MODES, "Modo de IGV")
    applies = igv.get("applies_to") or {}
    igv["applies_to"] = {k: bool(applies.get(k, False)) for k in ("delivery", "multi_store", "service_fee", "product_markup")}

    gw = c["gateways"]
    gw["group"] = _choice(gw.get("group"), GROUPS, "Grupo de la comision de pago")
    for name in GATEWAYS:
        g = gw.get(name) or {}
        gw[name] = {
            "mode": _choice(g.get("mode") or "cost", GATEWAY_MODES, f"Cobro de {name}"),
            "rate": _num(g.get("rate", 0), f"{name}: porcentaje", hi=0.5),
            "fixed": _num(g.get("fixed", 0), f"{name}: fijo"),
        }

    labels = ((c.get("display") or {}).get("labels") or {})
    c["display"] = {"labels": {
        key: (str(labels.get(key) or default)[:40]) for key, default in
        (("producto", "Productos"), ("envio", "Envío"), ("comision", "Comisión"))
    }}
    return c


# ---------------------------------------------------------------------------
# Piezas del calculo
# ---------------------------------------------------------------------------

def tariff_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """JSON de tarifas (cobertura incluida) con lo que el dueño cambio encima."""
    base = copy.deepcopy(_tariff_json())
    for key in DELIVERY_KEYS:
        if key in (cfg.get("delivery") or {}):
            base[key] = copy.deepcopy(cfg["delivery"][key])
    return base


def normalize_category_name(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.strip().lower()


def exempt_category_names(cfg: dict[str, Any]) -> set[str]:
    return {normalize_category_name(n) for n in cfg["service_fee"].get("exempt_categories") or [] if n}


def multi_store_surcharge(cfg: dict[str, Any], base_delivery_fee: float, branch_count: int) -> tuple[float, float]:
    """(monto, tasa acumulada). ValueError si se pasan del maximo de locales."""
    rates = cfg["multi_store"]["rates"]
    extra = max(0, int(branch_count) - 1)
    if extra > len(rates):
        raise ValueError(f"Un pedido puede juntar hasta {len(rates) + 1} locales.")
    rate = round(sum(rates[:extra]), 4)
    amount = round(max(0.0, float(base_delivery_fee)) * rate, 2)
    cap = float(cfg["multi_store"].get("max_amount") or 0)
    if cap > 0:
        amount = min(amount, round(cap, 2))
    return amount, rate


def tier_rate(cfg: dict[str, Any], tier_amount: float) -> float:
    for tier in cfg["service_fee"]["tiers"]:
        if tier["up_to"] is None or tier_amount <= float(tier["up_to"]):
            return float(tier["rate"])
    return float(cfg["service_fee"]["tiers"][-1]["rate"])


def service_fee_amount(cfg: dict[str, Any], base: float, tier_amount: float) -> float:
    sf = cfg["service_fee"]
    base = max(0.0, float(base))
    if sf["tier_mode"] == "progressive":
        fee = 0.0
        lower = 0.0
        for tier in sf["tiers"]:
            upper = math.inf if tier["up_to"] is None else float(tier["up_to"])
            slice_amount = max(0.0, min(base, upper) - lower)
            fee += slice_amount * float(tier["rate"])
            lower = upper
            if base <= upper:
                break
        return round(fee, 2)
    return round(base * tier_rate(cfg, tier_amount), 2)


def _gross_up(amount: float, rate: float, fixed: float) -> float:
    """Comision para que, tras descontar rate del total final y el fijo, llegue amount completo."""
    if amount <= 0:
        return 0.0
    rate = min(max(0.0, rate), 0.9)
    return round((amount * rate + fixed) / (1 - rate), 2)


def gateway_for(payment_method: str) -> tuple[str, str]:
    """(clave de la pasarela en la configuracion, proveedor que cobra)."""
    from config import METODO_YAPE_DIRECTO, payment_provider_for, yape_propio_configurado

    if payment_method == METODO_YAPE_DIRECTO and yape_propio_configurado():
        return "yape_directo", "yape_propio"
    method = "yape" if payment_method in ("yape", METODO_YAPE_DIRECTO) else "card"
    provider = payment_provider_for(method)
    if provider in ("izipay", "izipay_sdk"):
        return "izipay", provider
    return ("culqi_yape" if method == "yape" else "culqi_card"), provider


def gateway_real_cost(key: str, charged_total: float) -> float:
    """Lo que la pasarela le descuenta a ACME de un pago de charged_total."""
    costs = gateway_costs()
    igv = costs["igv_rate"]
    c = costs[key]
    total = max(0.0, float(charged_total))
    if total <= 0:
        return 0.0
    if key == "culqi_yape" and c.get("min_fee") and c.get("min_fee_below") and total < c["min_fee_below"]:
        return round(c["min_fee"], 2)
    fee = total * c["rate"] + c["fixed"]
    if c.get("igv"):
        fee *= 1 + igv
    return round(fee, 2)


def gateway_fee(cfg: dict[str, Any], payment_method: str, amount: float) -> dict[str, Any]:
    """Comision de pago que ve el cliente, segun como el dueño configuro esa pasarela."""
    from services_courier_quote import calculate_payment_processing_fee

    key, provider = gateway_for(payment_method)
    g = cfg["gateways"][key]
    mode = g["mode"]
    if key == "yape_directo":
        from config import yape_propio_aplica

        tentative = amount + (_gross_up(amount, g["rate"], g["fixed"]) if mode == "percent" else 0.0)
        if not yape_propio_aplica(payment_method, int(round(tentative * 100))):
            # Yape directo apagado o sobre el tope: se cotiza como Yape normal.
            return gateway_fee(cfg, "yape", amount)
    labels = {
        "yape_directo": "BCP (Yape empresa) y pasarela Accuracy Nexus",
        "izipay": "Izipay",
        "culqi_yape": "Culqi (Yape)",
        "culqi_card": "Culqi (tarjeta)",
    }
    if mode == "absorb":
        return {"key": key, "fee": 0.0, "rate": 0.0, "fixed": 0.0, "provider": provider,
                "note": f"{labels[key]}: la comision la asume ACME.", "tax_amount": 0.0, "mode": mode}
    if mode == "percent":
        fee = _gross_up(amount, g["rate"], g["fixed"])
        note = f"{labels[key]}: {g['rate'] * 100:.2f}% del total"
        if g["fixed"]:
            note += f" + S/ {g['fixed']:.2f}"
        return {"key": key, "fee": fee, "rate": g["rate"], "fixed": g["fixed"], "provider": provider,
                "note": note + ".", "tax_amount": 0.0, "mode": mode}
    # cost: la formula exacta de la pasarela (la de siempre).
    if key == "yape_directo":
        cost_rate = float(settings.yape_directo_cost_rate)
        fee = _gross_up(amount, cost_rate, 0.0)
        return {"key": key, "fee": fee, "rate": cost_rate, "fixed": 0.0, "provider": provider,
                "note": f"{labels[key]}: {cost_rate * 100:.2f}% del total.", "tax_amount": 0.0, "mode": mode}
    res = calculate_payment_processing_fee(amount, "yape" if key == "culqi_yape" else "card")
    return {**res, "key": key, "mode": mode}


# ---------------------------------------------------------------------------
# Calculo completo de un pedido
# ---------------------------------------------------------------------------

def _rule_for(cfg: dict[str, Any], merchant_id: str | None) -> dict[str, Any] | None:
    if not merchant_id:
        return None
    for rule in cfg.get("merchant_rules") or []:
        if rule["merchant_id"] == str(merchant_id):
            return rule
    return None


def compute_order_pricing(
    cfg: dict[str, Any],
    *,
    lines: list[dict[str, Any]],
    delivery_fee: float,
    branch_count: int = 1,
    tip: float = 0.0,
    payment_method: str = "card",
    fulfillment_type: str = "delivery",
) -> dict[str, Any]:
    """Precio de un pedido con la configuracion dada.

    lines: [{line_total, quantity, merchant_id, exempt}] con el precio del local.
    delivery_fee: tarifa courier base (sin recargo por locales).

    Devuelve los conceptos internos (para el gerente), los 3 grupos que ve el
    cliente y los campos de siempre de la cotizacion, que suman lo mismo para
    que las apps ya instaladas no se rompan.
    """
    igv_cfg = cfg["igv"]
    igv_rate = float(igv_cfg["rate"]) if igv_cfg["mode"] != "none" else 0.0
    sf_cfg = cfg["service_fee"]

    products = 0.0
    exempt_products = 0.0
    markup = 0.0
    fee_base_products = 0.0
    override_fee = 0.0
    merchant_exempt_products = 0.0
    for line in lines:
        amount = max(0.0, float(line.get("line_total") or 0))
        qty = max(0.0, float(line.get("quantity") or 0))
        products += amount
        rule = _rule_for(cfg, line.get("merchant_id"))
        is_exempt = bool(line.get("exempt"))
        if is_exempt:
            exempt_products += amount
        else:
            rate = cfg["product_markup"]["rate"]
            fixed = cfg["product_markup"]["fixed_per_unit"]
            if rule and rule.get("markup_rate") is not None:
                rate = rule["markup_rate"]
            if rule and rule.get("markup_fixed_per_unit") is not None:
                fixed = rule["markup_fixed_per_unit"]
            markup += amount * rate + qty * fixed
        if rule and rule.get("service_fee_exempt"):
            merchant_exempt_products += amount
            continue
        if rule and rule.get("service_fee_rate") is not None and not is_exempt:
            override_fee += amount * float(rule["service_fee_rate"])
            continue
        if not is_exempt:
            fee_base_products += amount
    products = round(products, 2)
    markup = round(markup, 2)
    # Pedido solo de categorias exentas: paga tarifa sobre todo (mantenimiento).
    if sf_cfg["exempt_only_orders_pay"] and products > 0 and exempt_products >= products - 0.001 and override_fee == 0:
        fee_base_products = round(products - merchant_exempt_products, 2)

    pickup = fulfillment_type == "pickup"
    delivery = 0.0 if pickup else round(max(0.0, float(delivery_fee)), 2)
    ms_amount, ms_rate = multi_store_surcharge(cfg, delivery, branch_count)

    # Tarifa de servicio
    service_fee = 0.0
    fee_base = 0.0
    fee_rate_label = 0.0
    if sf_cfg["enabled"]:
        parts = {"products": fee_base_products, "delivery": delivery, "multi_store": ms_amount}
        fee_base = round(sum(parts[b] for b in sf_cfg["base"]), 2)
        tier_amount = products if sf_cfg["tier_by"] == "products" else fee_base
        fee_rate_label = tier_rate(cfg, tier_amount)
        service_fee = service_fee_amount(cfg, fee_base, tier_amount) + round(override_fee, 2)
        if fee_base > 0 or override_fee > 0:
            service_fee += float(sf_cfg["fixed"])
            if sf_cfg["min"]:
                service_fee = max(service_fee, float(sf_cfg["min"]))
            if sf_cfg["max"]:
                service_fee = min(service_fee, float(sf_cfg["max"]))
        service_fee = round(service_fee, 2)

    # IGV por concepto
    taxable = {
        "delivery": delivery,
        "multi_store": ms_amount,
        "service_fee": service_fee,
        "product_markup": markup,
    }
    applicable = {k: v for k, v in taxable.items() if igv_rate and igv_cfg["applies_to"].get(k) and v > 0}
    taxable_sum = round(sum(applicable.values()), 2)
    if not applicable:
        igv_total = 0.0
    elif igv_cfg["mode"] == "added":
        igv_total = round(taxable_sum * igv_rate, 2)
    else:
        igv_total = round(taxable_sum - taxable_sum / (1 + igv_rate), 2)
    # Reparto del IGV por concepto (para ponerlo en su grupo); el redondeo
    # sobrante va al concepto mas grande, asi la suma es exacta.
    igv_by = {k: (round(igv_total * v / taxable_sum, 2) if k in applicable else 0.0) for k, v in taxable.items()}
    if applicable:
        biggest = max(applicable, key=lambda k: applicable[k])
        igv_by[biggest] = round(igv_by[biggest] + igv_total - sum(igv_by.values()), 2)
    igv_added = igv_cfg["mode"] == "added"
    igv_on_top = igv_total if igv_added else 0.0

    tip = round(max(0.0, float(tip)), 2)
    before_gateway = round(products + markup + delivery + ms_amount + service_fee + igv_on_top + tip, 2)
    gw = gateway_fee(cfg, payment_method, before_gateway)
    gateway = round(float(gw["fee"]), 2)
    total = round(before_gateway + gateway, 2)
    gateway_cost = gateway_real_cost(gw["key"], total)

    # Grupos que ve el cliente
    groups = {g: 0.0 for g in GROUPS}
    details: dict[str, list[str]] = {g: [] for g in GROUPS}

    def add(group: str, amount: float, label: str) -> None:
        if amount:
            groups[group] = round(groups[group] + amount, 2)
            details[group].append(label)

    add("producto", products, "precio del local")
    add(cfg["product_markup"]["group"], markup + (igv_by["product_markup"] if igv_added else 0.0), "condición en productos")
    add(cfg["delivery"]["group"], delivery + (igv_by["delivery"] if igv_added else 0.0), "envío")
    add(cfg["multi_store"]["group"], ms_amount + (igv_by["multi_store"] if igv_added else 0.0), f"recargo por {branch_count} locales")
    add(sf_cfg["group"], service_fee + (igv_by["service_fee"] if igv_added else 0.0), "tarifa de servicio")
    add(cfg["gateways"]["group"], gateway, "comisión de pago")
    labels = cfg["display"]["labels"]
    client_groups = [
        {"key": g, "label": labels[g], "amount": groups[g]}
        for g in GROUPS
        if groups[g] > 0 or g == "producto"
    ]

    acme_gateway_margin = round(gateway - gateway_cost, 2)
    igv_inside = 0.0 if igv_added else igv_total
    acme_net = round(markup + delivery + ms_amount + service_fee + acme_gateway_margin - igv_inside, 2)
    concepts = [
        {"key": "products", "label": "Productos (precio del local)", "amount": products, "to": "Local", "group": "producto"},
        {"key": "product_markup", "label": "Condición en productos", "amount": markup, "to": "ACME", "group": cfg["product_markup"]["group"]},
        {"key": "delivery", "label": "Envío", "amount": delivery, "to": "ACME / moto", "group": cfg["delivery"]["group"]},
        {"key": "multi_store", "label": f"Recargo por locales ({ms_rate * 100:.0f}%)", "amount": ms_amount, "to": "ACME / moto", "group": cfg["multi_store"]["group"]},
        {"key": "service_fee", "label": f"Tarifa de servicio ({fee_rate_label * 100:g}% de S/ {fee_base:.2f})", "amount": service_fee, "to": "ACME", "group": sf_cfg["group"]},
        {"key": "igv", "label": f"IGV {igv_rate * 100:g}% ({'encima' if igv_added else 'incluido' if igv_total else 'no aplica'})", "amount": igv_total, "to": "SUNAT", "group": None, "added": igv_added},
        {"key": "tip", "label": "Propina", "amount": tip, "to": "Repartidor", "group": None},
        {"key": "gateway", "label": f"Comisión de pago ({gw['note']})", "amount": gateway, "to": "Pasarela + ACME", "group": cfg["gateways"]["group"]},
    ]
    payout = [
        {"to": "Local", "amount": products},
        {"to": "ACME (neto, ya sin IGV ni pasarela)", "amount": acme_net},
        {"to": "SUNAT (IGV)", "amount": igv_total},
        {"to": "Pasarela (lo que descuenta)", "amount": gateway_cost},
        {"to": "Repartidor (propina)", "amount": tip},
    ]

    return {
        "products": products,
        "exempt_products": round(exempt_products, 2),
        "product_markup": markup,
        "delivery_fee": delivery,
        "multi_store_surcharge": ms_amount,
        "multi_store_rate": ms_rate,
        "service_fee": service_fee,
        "service_fee_base": fee_base,
        "service_fee_rate": fee_rate_label,
        "igv_rate": igv_rate,
        "igv_amount": igv_total,
        "igv_added": igv_added,
        "igv_by": igv_by,
        "taxable_base": taxable_sum,
        "tip": tip,
        "gateway": gw,
        "gateway_fee": gateway,
        "gateway_cost": gateway_cost,
        "acme_gateway_margin": acme_gateway_margin,
        "acme_net": acme_net,
        "total": total,
        "client_groups": client_groups,
        "client_group_details": details,
        "concepts": concepts,
        "payout": payout,
    }
