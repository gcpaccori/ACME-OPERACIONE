from __future__ import annotations

import requests

from config import settings


class SupabaseAuthError(Exception):
    pass


def verify_supabase_user(access_token: str) -> str:
    """Valida el access token contra Supabase Auth y devuelve auth.users.id."""
    if not settings.supabase_url:
        raise SupabaseAuthError("Falta SUPABASE_URL para validar la sesion.")

    api_key = settings.supabase_key or settings.supabase_anon_jwt or settings.supabase_service_role_key
    if not api_key:
        raise SupabaseAuthError("Falta SUPABASE_ANON_KEY para validar la sesion.")

    response = requests.get(
        f"{settings.supabase_url.rstrip('/')}/auth/v1/user",
        headers={
            "apikey": api_key,
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
        timeout=10,
    )

    if response.status_code >= 400:
        raise SupabaseAuthError("Sesion invalida o expirada.")

    data = response.json()
    user_id = str(data.get("id") or "")
    if not user_id:
        raise SupabaseAuthError("Supabase Auth no devolvio un usuario valido.")
    return user_id
