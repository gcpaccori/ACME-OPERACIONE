"""Panel de conciliacion de la pasarela Yape propia, solo para admins de ACME.

La web no puede hablar directo con acme-yape (la clave no va al navegador):
esta ruta comprueba que quien pide es admin de plataforma, trae el periodo de
acme-yape y le pega a cada cobro los datos del pedido y del cliente en ACME.
Solo lectura.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

import requests
from fastapi import APIRouter, Header, HTTPException

import services_yape_propio
from config import settings, yape_propio_configurado
from services_supabase_auth import SupabaseAuthError, verify_supabase_user
from services_yape_propio import YapePropioError

router = APIRouter(prefix="/api/admin/yape", tags=["admin-yape"])
logger = logging.getLogger(__name__)

# Los mismos roles que abren el alcance "plataforma" en la web (portalAccess.ts).
ROLES_ADMIN = {"admin", "super_admin"}
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def _sb_get(table: str, params: dict[str, str]) -> list[dict[str, Any]]:
    key = settings.supabase_service_role_key or ""
    resp = requests.get(
        f"{settings.supabase_url.rstrip('/')}/rest/v1/{table}",
        headers={"apikey": key, "Authorization": f"Bearer {key}", "Accept": "application/json"},
        params=params,
        timeout=10,
    )
    if resp.status_code >= 400:
        raise HTTPException(status_code=500, detail=f"Supabase {table}: {resp.text[:300]}")
    return resp.json() or []


def _en(ids: set[str]) -> str:
    return f"in.({','.join(sorted(ids))})"


def _es_admin(user_id: str) -> bool:
    perfil = _sb_get("profiles", {"select": "default_role", "user_id": f"eq.{user_id}", "limit": "1"})
    if perfil and str(perfil[0].get("default_role") or "").strip().lower() in ROLES_ADMIN:
        return True
    asignados = _sb_get("user_roles", {"select": "role_id", "user_id": f"eq.{user_id}"})
    role_ids = {str(r["role_id"]) for r in asignados if r.get("role_id")}
    if not role_ids:
        return False
    roles = _sb_get("roles", {"select": "code", "id": _en(role_ids)})
    return any(str(r.get("code") or "").strip().lower() in ROLES_ADMIN for r in roles)


def _verificar_admin(authorization: str | None) -> str:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="Inicia sesion como administrador.")
    try:
        # Se valida contra Supabase Auth: un token armado a mano no pasa.
        user_id = verify_supabase_user(token.strip())
    except SupabaseAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    if not _es_admin(user_id):
        raise HTTPException(status_code=403, detail="Solo un administrador de ACME puede ver los Yapes.")
    return user_id


def _pedidos(referencias: set[str]) -> dict[str, dict[str, Any]]:
    ids = {r for r in referencias if UUID.match(r)}
    if not ids:
        return {}
    filas = _sb_get("orders", {
        "select": "id,order_code,status,payment_status,total,currency,customer_id,merchant_id,created_at",
        "id": _en(ids),
    })
    return {str(f["id"]): f for f in filas}


def _clientes(ids: set[str]) -> dict[str, dict[str, Any]]:
    ids = {i for i in ids if i and UUID.match(i)}
    if not ids:
        return {}
    filas = _sb_get("profiles", {"select": "user_id,full_name,email,phone", "user_id": _en(ids)})
    return {str(f["user_id"]): f for f in filas}


def _comercios(ids: set[str]) -> dict[str, str]:
    ids = {i for i in ids if i and UUID.match(i)}
    if not ids:
        return {}
    filas = _sb_get("merchants", {"select": "id,trade_name,legal_name", "id": _en(ids)})
    return {str(f["id"]): str(f.get("trade_name") or f.get("legal_name") or "") for f in filas}


def _enriquecer(datos: dict[str, Any]) -> dict[str, Any]:
    cobros = datos.get("cobros") or []
    referencias = {str(c.get("referencia") or "") for c in cobros}
    referencias |= {str(d.get("referencia") or "") for d in datos.get("devoluciones") or []}
    referencias |= {str(r.get("referencia") or "") for r in datos.get("reclamos") or []}
    pedidos = _pedidos(referencias)
    clientes = _clientes({str(c.get("cliente_id") or "") for c in cobros}
                         | {str(p.get("customer_id") or "") for p in pedidos.values()})
    comercios = _comercios({str(p.get("merchant_id") or "") for p in pedidos.values()})

    vistos: dict[str, dict[str, Any]] = {}
    for ref, p in pedidos.items():
        cliente = clientes.get(str(p.get("customer_id") or "")) or {}
        vistos[ref] = {
            "id": ref,
            "codigo": p.get("order_code"),
            "estado": p.get("status"),
            "estado_pago": p.get("payment_status"),
            "total": p.get("total"),
            "creado_en": p.get("created_at"),
            "comercio": comercios.get(str(p.get("merchant_id") or "")),
            "cliente_nombre": cliente.get("full_name"),
            "cliente_correo": cliente.get("email"),
            "cliente_celular": cliente.get("phone"),
        }
    datos["pedidos"] = vistos
    datos["clientes"] = {
        uid: {"nombre": c.get("full_name"), "correo": c.get("email"), "celular": c.get("phone")}
        for uid, c in clientes.items()
    }
    return datos


@router.get("/conciliacion")
def conciliacion(desde: datetime, hasta: datetime, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Todo lo que paso con Yape directo en un periodo, con el pedido de cada cobro."""
    _verificar_admin(authorization)
    if not yape_propio_configurado():
        raise HTTPException(status_code=503, detail="Yape directo no esta configurado en este servidor.")
    try:
        datos = services_yape_propio.conciliacion(desde.isoformat(), hasta.isoformat())
    except YapePropioError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.mensaje) from exc
    try:
        return _enriquecer(datos)
    except HTTPException:
        # Sin los datos de ACME el panel igual sirve: los yapeos y cobros son lo importante.
        logger.exception("conciliacion yape: no se pudo leer pedidos de ACME")
        datos["pedidos"], datos["clientes"], datos["aviso"] = {}, {}, "No se pudieron leer los pedidos de ACME."
        return datos
