import copy

import pytest

import services_pricing as sp
from config import settings


class Resp:
    def __init__(self, status, data):
        self.status_code = status
        self._data = data
        self.text = str(data)

    def json(self):
        return self._data


@pytest.fixture
def fake_db(monkeypatch):
    """Supabase de mentira: pricing_config, su historial y pedidos."""
    monkeypatch.setattr(settings, "supabase_url", "https://x.supabase.co")
    monkeypatch.setattr(settings, "supabase_service_role_key", "k")
    db = {"config": None, "history": [], "orders": [], "calls": []}

    def post(url, headers=None, params=None, json=None, timeout=None):
        db["calls"].append(url.rsplit("/", 1)[-1])
        if url.endswith("pricing_config_history"):
            row = {**json, "id": len(db["history"]) + 1, "created_at": "2026-11-01T00:00:00Z"}
            db["history"].append(row)
            return Resp(201, [row])
        db["config"] = json
        return Resp(201, [json])

    def get(url, headers=None, params=None, timeout=None):
        table = url.rsplit("/", 1)[-1]
        if table == "pricing_config":
            return Resp(200, [db["config"]] if db["config"] else [])
        if table == "pricing_config_history":
            rows = db["history"]
            if params.get("id"):
                rows = [r for r in rows if f"eq.{r['id']}" == params["id"]]
            return Resp(200, list(reversed(rows)))
        if table == "orders":
            return Resp(200, db["orders"])
        return Resp(200, [])

    monkeypatch.setattr(sp.requests, "post", post)
    monkeypatch.setattr(sp.requests, "get", get)
    sp.clear_cache()
    yield db
    sp.clear_cache()


def test_cada_guardado_es_una_version_con_lo_que_cambio(fake_db):
    cfg = copy.deepcopy(sp.default_config())
    cfg["service_fee"]["tiers"][0]["rate"] = 0.10
    out = sp.save_pricing_config(cfg, "u1", "Bajamos la tarifa")
    assert out["version_id"] == 1
    # El historial se escribe antes que la configuracion vigente.
    assert fake_db["calls"] == ["pricing_config_history", "pricing_config"]
    assert fake_db["config"]["version_id"] == 1
    hist = fake_db["history"][0]
    assert hist["note"] == "Bajamos la tarifa"
    assert hist["previous_version_id"] is None
    cambio = [c for c in hist["changes"] if c["path"] == "service_fee.tiers"][0]
    assert cambio["label"] == "Tarifa de servicio › tramos"
    assert cambio["before"][0]["rate"] == 0.12 and cambio["after"][0]["rate"] == 0.10

    cfg2 = copy.deepcopy(cfg)
    cfg2["igv"]["mode"] = "included"
    sp.save_pricing_config(cfg2, "u1")
    assert fake_db["history"][1]["previous_version_id"] == 1
    assert [c["path"] for c in fake_db["history"][1]["changes"]] == ["igv.mode"]
    assert sp.load_pricing_config_with_version()[1] == 2


def test_sin_historial_no_se_guarda(fake_db, monkeypatch):
    monkeypatch.setattr(sp.requests, "post", lambda *a, **k: Resp(404, {"message": "no existe"}))
    with pytest.raises(RuntimeError):
        sp.save_pricing_config(sp.default_config(), "u1")


def test_cambios_en_el_mapa_se_resumen():
    a = sp.default_config()
    b = copy.deepcopy(a)
    b["delivery"]["sector_pricing"]["points"][0]["fee"] = 99
    b["delivery"]["sector_pricing"]["points"].append({"lat": 1, "lng": 1, "fee": 6, "sector": "x"})
    [c] = sp.describe_changes(a, b)
    assert "1 con otro precio" in c["after"] and "1 nuevos" in c["after"]


def test_desglose_del_pedido_y_cuadre_por_version(fake_db):
    cfg = sp.default_config()
    priced = sp.compute_order_pricing(
        cfg, lines=[{"line_total": 8.0, "quantity": 1}], delivery_fee=7.0, payment_method="card",
    )
    b = sp.pricing_breakdown(priced, 3)
    assert b["version"] == 3 and b["total"] == priced["total"]
    # Lo que se reparte suma lo que pago el cliente.
    assert round(sum(p["amount"] for p in b["payout"]), 2) == priced["total"]

    fake_db["orders"] = [
        {"pricing_version": 3, "total": priced["total"], "payment_status": "paid", "status": "delivered", "pricing_breakdown": b},
        {"pricing_version": 3, "total": priced["total"], "payment_status": "pending", "status": "delivered", "pricing_breakdown": b},
        {"pricing_version": 3, "total": 50, "payment_status": "pending", "status": "cancelled", "pricing_breakdown": b},
        {"pricing_version": 3, "total": 50, "payment_status": "refunded", "status": "delivered", "pricing_breakdown": b},
    ]
    s = sp.order_stats_by_version([3])["3"]
    assert s["orders"] == 4 and s["paid_orders"] == 2
    assert s["paid_total"] == round(2 * priced["total"], 2)
    assert s["payout"]["Local"] == 16.0


def test_volver_a_una_version(fake_db, monkeypatch):
    import routes_admin_pricing as rap

    monkeypatch.setattr(rap, "_verificar_admin", lambda auth: "u1")
    cfg = copy.deepcopy(sp.default_config())
    cfg["igv"]["mode"] = "none"
    sp.save_pricing_config(cfg, "u1")
    sp.save_pricing_config(sp.default_config(), "u1")
    out = rap.restore_version(1, None, authorization="Bearer x")
    assert out["version_id"] == 3
    assert fake_db["config"]["config"]["igv"]["mode"] == "none"
    assert fake_db["history"][2]["note"] == "Se volvio a la version 1."
