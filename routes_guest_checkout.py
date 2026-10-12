"""
Compra como invitado.

El cliente deja nombre, celular y correo en el carrito y paga sin registrarse.
Por detras se le crea una cuenta de cliente confirmada con una contrasena al
azar que nadie conoce, y se devuelve su sesion. Asi la cotizacion, el pedido,
el pago y el seguimiento siguen pasando por la misma validacion de sesion que
una cuenta normal. Si mas adelante quiere entrar, usa "olvide mi contrasena"
con su correo.

Un correo que ya tiene cuenta nunca recibe sesion por aqui (409): esa persona
tiene que ingresar con su contrasena, asi nadie entra a la cuenta de otro
escribiendo su correo.
"""
from __future__ import annotations

import logging
import re
import secrets
import threading
import time
from collections import deque

import requests
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from config import settings

router = APIRouter(prefix="/api/courier", tags=["courier-guest"])
logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Celular peruano: 9 digitos que empiezan en 9, con o sin +51.
PHONE_RE = re.compile(r"^(?:\+?51)?(9\d{8})$")

# Tope por IP (por instancia del servidor) para frenar la creacion masiva de cuentas.
RATE_LIMIT = 5
RATE_WINDOW_SECONDS = 600
_attempts: dict[str, deque[float]] = {}
_attempts_lock = threading.Lock()


class GuestSessionRequest(BaseModel):
    email: str = Field(..., max_length=254)
    nombre: str = Field(..., min_length=2, max_length=80)
    telefono: str = Field(..., max_length=20)
    acepta_terminos: bool
    version_textos_legales: str | None = Field(default=None, max_length=32)


class GuestSessionResponse(BaseModel):
    access_token: str
    refresh_token: str
    expires_in: int
    user_id: str


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _check_rate_limit(ip: str) -> None:
    now = time.monotonic()
    with _attempts_lock:
        bucket = _attempts.setdefault(ip, deque())
        while bucket and now - bucket[0] > RATE_WINDOW_SECONDS:
            bucket.popleft()
        if len(bucket) >= RATE_LIMIT:
            raise HTTPException(status_code=429, detail="Demasiados intentos. Espera unos minutos e intenta de nuevo.")
        bucket.append(now)


def normalize_phone(value: str) -> str | None:
    digits = re.sub(r"[\s\-()]", "", value or "")
    match = PHONE_RE.match(digits)
    return match.group(1) if match else None


def _auth_url(path: str) -> str:
    return f"{settings.supabase_url.rstrip('/')}/auth/v1/{path}"


@router.post("/guest-session", response_model=GuestSessionResponse)
def create_guest_session(payload: GuestSessionRequest, request: Request):
    service_key = settings.supabase_service_role_key
    anon_key = settings.supabase_key or settings.supabase_anon_jwt
    if not settings.supabase_url or not service_key or not anon_key:
        raise HTTPException(status_code=503, detail="La compra como invitado no esta disponible ahora.")

    email = payload.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="Escribe un correo valido.")
    phone = normalize_phone(payload.telefono)
    if not phone:
        raise HTTPException(status_code=422, detail="Escribe un celular valido de 9 digitos.")
    nombre = " ".join(payload.nombre.split())
    if len(nombre) < 2:
        raise HTTPException(status_code=422, detail="Escribe tu nombre.")
    if not payload.acepta_terminos:
        raise HTTPException(status_code=422, detail="Debes aceptar los terminos y la politica de privacidad.")

    _check_rate_limit(_client_ip(request))

    password = secrets.token_urlsafe(32)
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        created = requests.post(
            _auth_url("admin/users"),
            headers={"apikey": service_key, "Authorization": f"Bearer {service_key}", "Content-Type": "application/json"},
            json={
                "email": email,
                "password": password,
                "email_confirm": True,
                "user_metadata": {
                    "full_name": nombre,
                    "phone": phone,
                    "guest_checkout": True,
                    "terms_accepted_at": now_iso,
                    "legal_texts_version": payload.version_textos_legales,
                    "marketing_opt_in": False,
                },
            },
            timeout=15,
        )
    except requests.RequestException as exc:
        logger.error("guest_session_create_unavailable: %s", exc)
        raise HTTPException(status_code=503, detail="No pudimos preparar tu compra. Intenta de nuevo.") from exc

    if created.status_code in (409, 422) or (
        created.status_code >= 400 and "already" in created.text.lower()
    ):
        raise HTTPException(
            status_code=409,
            detail="Ese correo ya tiene una cuenta. Ingresa con tu contrasena para continuar.",
        )
    if created.status_code >= 400:
        logger.error("guest_session_create_failed status=%s body=%s", created.status_code, created.text[:300])
        raise HTTPException(status_code=502, detail="No pudimos preparar tu compra. Intenta de nuevo.")

    try:
        token = requests.post(
            _auth_url("token?grant_type=password"),
            headers={"apikey": anon_key, "Content-Type": "application/json"},
            json={"email": email, "password": password},
            timeout=15,
        )
    except requests.RequestException as exc:
        logger.error("guest_session_token_unavailable: %s", exc)
        raise HTTPException(status_code=503, detail="No pudimos preparar tu compra. Intenta de nuevo.") from exc

    data = token.json() if token.headers.get("content-type", "").startswith("application/json") else {}
    if token.status_code >= 400 or not data.get("access_token"):
        logger.error("guest_session_token_failed status=%s", token.status_code)
        raise HTTPException(status_code=502, detail="No pudimos preparar tu compra. Intenta de nuevo.")

    user_id = str((data.get("user") or {}).get("id") or "")
    logger.info("guest_session_created user=%s", user_id)
    return GuestSessionResponse(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token", ""),
        expires_in=int(data.get("expires_in") or 3600),
        user_id=user_id,
    )
