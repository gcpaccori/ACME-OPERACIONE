"""Pruebas de la compra como invitado (sin red: Supabase Auth simulado)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

import routes_guest_checkout  # noqa: E402
from config import settings  # noqa: E402
from main import app  # noqa: E402

client = TestClient(app)

VALID = {
    "email": "Cliente@Correo.pe",
    "nombre": "  Ana   Quispe ",
    "telefono": "+51 987 654 321",
    "acepta_terminos": True,
    "version_textos_legales": "2026-10",
}


class FakeResponse:
    def __init__(self, status_code: int, body: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._body = body or {}
        self.text = text or str(body)
        self.headers = {"content-type": "application/json"}

    def json(self):
        return self._body


@pytest.fixture(autouse=True)
def supabase_config(monkeypatch):
    monkeypatch.setattr(settings, "supabase_url", "https://example.supabase.co")
    monkeypatch.setattr(settings, "supabase_service_role_key", "service")
    monkeypatch.setattr(settings, "supabase_key", "anon")
    routes_guest_checkout._attempts.clear()


def test_normalize_phone():
    assert routes_guest_checkout.normalize_phone("+51 987-654-321") == "987654321"
    assert routes_guest_checkout.normalize_phone("987654321") == "987654321"
    assert routes_guest_checkout.normalize_phone("12345") is None
    assert routes_guest_checkout.normalize_phone("887654321") is None


def test_creates_confirmed_user_and_returns_session(monkeypatch):
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append((url, json))
        if url.endswith("admin/users"):
            return FakeResponse(200, {"id": "user-1"})
        return FakeResponse(200, {"access_token": "at", "refresh_token": "rt", "expires_in": 3600, "user": {"id": "user-1"}})

    monkeypatch.setattr(routes_guest_checkout.requests, "post", fake_post)
    response = client.post("/api/courier/guest-session", json=VALID)

    assert response.status_code == 200
    assert response.json() == {"access_token": "at", "refresh_token": "rt", "expires_in": 3600, "user_id": "user-1"}
    created = calls[0][1]
    assert created["email"] == "cliente@correo.pe"
    assert created["email_confirm"] is True
    assert created["user_metadata"]["full_name"] == "Ana Quispe"
    assert created["user_metadata"]["phone"] == "987654321"
    assert created["user_metadata"]["guest_checkout"] is True
    # La contrasena es al azar y es la misma con la que se pide la sesion.
    assert len(created["password"]) >= 32
    assert calls[1][1]["password"] == created["password"]


def test_existing_email_never_gets_a_session(monkeypatch):
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(url)
        return FakeResponse(422, {"error_code": "email_exists"}, text="A user with this email address has already been registered")

    monkeypatch.setattr(routes_guest_checkout.requests, "post", fake_post)
    response = client.post("/api/courier/guest-session", json=VALID)

    assert response.status_code == 409
    assert len(calls) == 1  # no se pidio ningun token


@pytest.mark.parametrize(
    "field,value",
    [("email", "no-es-correo"), ("telefono", "12345"), ("acepta_terminos", False)],
)
def test_rejects_invalid_input(monkeypatch, field, value):
    monkeypatch.setattr(routes_guest_checkout.requests, "post", lambda *a, **k: pytest.fail("no debe llamar a Supabase"))
    response = client.post("/api/courier/guest-session", json={**VALID, field: value})
    assert response.status_code == 422


def test_rate_limit(monkeypatch):
    monkeypatch.setattr(
        routes_guest_checkout.requests,
        "post",
        lambda url, **k: FakeResponse(422, {"error_code": "email_exists"}, text="already registered"),
    )
    codes = [client.post("/api/courier/guest-session", json=VALID).status_code for _ in range(6)]
    assert codes[:5] == [409] * 5
    assert codes[5] == 429
