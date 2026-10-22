"""Cliente de la pasarela Yape propia (repo acme-yape).

acme-yape abre una sesion con un monto unico por pedido, detecta el yapeo con
el celular receptor y, cuando la sesion queda pagada, avisa a
/api/webhooks/yape-propio con un aviso firmado (HMAC-SHA256 de
"timestamp.cuerpo" con YAPE_PROPIO_WEBHOOK_SECRETO).
"""
from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any

import requests

from config import settings

# Un aviso firmado vale unos minutos, para que no se pueda reenviar despues.
MAX_DESFASE_SEGUNDOS = 300


class YapePropioError(Exception):
    def __init__(self, status_code: int, mensaje: str, codigo: str | None = None):
        super().__init__(mensaje)
        self.status_code = status_code
        self.mensaje = mensaje
        self.codigo = codigo


def abrir_sesion(referencia: str, cliente_id: str | None, monto_centimos: int,
                 nombre_pagador: str | None = None) -> dict[str, Any]:
    """Abre (o reusa) la sesion de cobro del pedido en acme-yape."""
    try:
        resp = requests.post(
            f"{settings.yape_propio_url.rstrip('/')}/v1/sesiones",
            headers={"X-Api-Key": settings.yape_propio_api_key},
            json={"referencia": referencia, "cliente_id": cliente_id, "monto_centimos": monto_centimos,
                  "nombre_pagador": (nombre_pagador or "").strip() or None},
            timeout=10,
        )
    except requests.RequestException as exc:
        raise YapePropioError(502, "El pago con Yape no esta disponible por ahora. Intenta en un momento.") from exc

    if resp.status_code < 300:
        return resp.json()

    try:
        detalle = resp.json().get("detail")
    except ValueError:
        detalle = None
    if isinstance(detalle, dict):
        raise YapePropioError(resp.status_code, str(detalle.get("mensaje") or "No se pudo abrir el pago con Yape."),
                              detalle.get("codigo"))
    raise YapePropioError(502 if resp.status_code >= 500 else resp.status_code,
                          str(detalle or "No se pudo abrir el pago con Yape."))


def firma_valida(timestamp: str | None, firma: str | None, cuerpo: bytes, ahora: float | None = None) -> bool:
    secreto = settings.yape_propio_webhook_secreto
    if not secreto or not timestamp or not firma:
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    if abs((ahora if ahora is not None else time.time()) - ts) > MAX_DESFASE_SEGUNDOS:
        return False
    esperado = hmac.new(secreto.encode(), timestamp.encode() + b"." + cuerpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, firma.lower())
