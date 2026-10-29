from datetime import datetime, timedelta, timezone

from services_branch_hours import branch_closed_reason

LIMA = timezone(timedelta(hours=-5))
# 29/10/2026 es jueves (day_of_week = 4).
JUEVES_MEDIODIA = datetime(2026, 10, 29, 12, 2, tzinfo=LIMA)


def _horario(abre: str, cierra: str, cerrado: bool = False):
    return [
        {"day_of_week": d, "open_time": abre, "close_time": cierra, "is_closed": cerrado}
        for d in range(7)
    ]


def _local(**extra):
    base = {
        "name": "Artesano",
        "status": "active",
        "accepts_orders": True,
        "merchant_branch_status": {"is_open": True, "accepting_orders": True, "pause_reason": None},
        "merchant_branch_hours": _horario("08:00:00", "22:00:00"),
        "merchant_branch_closures": [],
    }
    base.update(extra)
    return base


def test_abierto_dentro_del_horario():
    assert branch_closed_reason(_local(), JUEVES_MEDIODIA) is None


def test_fuera_de_horario_dice_a_que_hora_abre():
    local = _local(merchant_branch_hours=_horario("16:00:00", "23:50:00"))
    assert branch_closed_reason(local, JUEVES_MEDIODIA) == "Artesano está cerrado ahora. Abre hoy a las 4:00 p. m."


def test_cierre_a_las_00_es_medianoche():
    local = _local(merchant_branch_hours=_horario("17:00:00", "00:00:00"))
    noche = datetime(2026, 10, 29, 23, 30, tzinfo=LIMA)
    assert branch_closed_reason(local, noche) is None
    assert branch_closed_reason(local, JUEVES_MEDIODIA) is not None


def test_horario_que_cruza_la_medianoche():
    local = _local(merchant_branch_hours=_horario("18:00:00", "02:00:00"))
    madrugada = datetime(2026, 10, 30, 1, 0, tzinfo=LIMA)
    assert branch_closed_reason(local, madrugada) is None
    assert branch_closed_reason(local, datetime(2026, 10, 30, 3, 0, tzinfo=LIMA)) is not None


def test_dia_marcado_cerrado_avisa_el_siguiente():
    horas = _horario("08:00:00", "22:00:00")
    horas[4]["is_closed"] = True
    local = _local(merchant_branch_hours=horas)
    assert branch_closed_reason(local, JUEVES_MEDIODIA) == "Artesano está cerrado ahora. Abre mañana a las 8:00 a. m."


def test_interruptores_y_cierre_temporal():
    assert branch_closed_reason(_local(accepts_orders=False), JUEVES_MEDIODIA)
    assert branch_closed_reason(
        _local(merchant_branch_status=[{"is_open": False, "accepting_orders": True, "pause_reason": "Inventario"}]),
        JUEVES_MEDIODIA,
    ) == "Artesano está cerrado ahora. Inventario."
    cierre = {"starts_at": "2026-10-29T10:00:00-05:00", "ends_at": "2026-10-29T14:00:00-05:00"}
    assert branch_closed_reason(_local(merchant_branch_closures=[cierre]), JUEVES_MEDIODIA)


def test_sin_horario_ni_estado_queda_abierto():
    local = _local(merchant_branch_hours=[], merchant_branch_status=None)
    assert branch_closed_reason(local, JUEVES_MEDIODIA) is None
