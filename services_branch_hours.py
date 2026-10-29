"""Si un local puede recibir pedidos ahora, segun sus interruptores y su horario.

Es la misma regla que muestra la web: el local tiene que estar activo, con
"acepta pedidos" y "abierto" encendidos, sin un cierre temporal en curso y
dentro de su horario del dia en hora de Lima. Un cierre a las 00:00 se toma
como medianoche (fin del dia), y un cierre menor que la apertura cruza la
medianoche (por ejemplo 18:00 a 02:00).
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any

LIMA = timezone(timedelta(hours=-5))

_DIAS = ["domingo", "lunes", "martes", "miércoles", "jueves", "viernes", "sábado"]


def _parse_time(value: Any) -> time | None:
    if not value:
        return None
    try:
        parts = [int(p) for p in str(value).split(":")[:3]]
    except ValueError:
        return None
    while len(parts) < 3:
        parts.append(0)
    return time(parts[0] % 24, parts[1], parts[2])


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _one(value: Any) -> dict[str, Any]:
    if isinstance(value, list):
        return value[0] if value else {}
    return value or {}


def _dow(moment: datetime) -> int:
    # Postgres y la web: 0 = domingo ... 6 = sabado.
    return (moment.weekday() + 1) % 7


def _minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def _hhmm(value: time) -> str:
    hour = value.hour % 12 or 12
    suffix = "a. m." if value.hour < 12 else "p. m."
    return f"{hour}:{value.minute:02d} {suffix}"


def _row_for(hours: list[dict[str, Any]], dow: int) -> dict[str, Any] | None:
    for row in hours:
        try:
            if int(row.get("day_of_week")) == dow:
                return row
        except (TypeError, ValueError):
            continue
    return None


def _within(row: dict[str, Any] | None, minute_of_day: int, previous_day: bool = False) -> bool:
    if not row or row.get("is_closed"):
        return False
    opens = _parse_time(row.get("open_time"))
    closes = _parse_time(row.get("close_time"))
    if opens is None or closes is None:
        return False
    start = _minutes(opens)
    end = _minutes(closes)
    if end == 0:
        end = 24 * 60
    if end > start:
        return not previous_day and start <= minute_of_day < end
    # Cruza la medianoche: hoy desde la apertura, y la madrugada siguiente
    # hasta el cierre cuenta para el horario del dia anterior.
    return minute_of_day < end if previous_day else minute_of_day >= start


def _next_opening(hours: list[dict[str, Any]], now: datetime) -> str | None:
    minute_of_day = now.hour * 60 + now.minute
    for offset in range(0, 7):
        dow = (_dow(now) + offset) % 7
        row = _row_for(hours, dow)
        if not row or row.get("is_closed"):
            continue
        opens = _parse_time(row.get("open_time"))
        if opens is None:
            continue
        if offset == 0:
            if _minutes(opens) > minute_of_day:
                return f"Abre hoy a las {_hhmm(opens)}"
            continue
        if offset == 1:
            return f"Abre mañana a las {_hhmm(opens)}"
        return f"Abre el {_DIAS[dow]} a las {_hhmm(opens)}"
    return None


def branch_closed_reason(branch: dict[str, Any], now: datetime | None = None) -> str | None:
    """None si el local puede recibir pedidos ahora; si no, el motivo para el cliente."""
    moment = (now or datetime.now(timezone.utc)).astimezone(LIMA)
    name = str(branch.get("name") or "El local").strip() or "El local"

    status = _one(branch.get("merchant_branch_status"))
    if str(branch.get("status") or "active") != "active" or branch.get("accepts_orders") is False:
        return f"{name} no está recibiendo pedidos por ahora."
    if status and (status.get("is_open") is False or status.get("accepting_orders") is False):
        reason = str(status.get("pause_reason") or "").strip()
        return f"{name} está cerrado ahora." + (f" {reason}." if reason else "")

    for closure in branch.get("merchant_branch_closures") or []:
        starts = _parse_ts(closure.get("starts_at"))
        ends = _parse_ts(closure.get("ends_at"))
        if starts and ends and starts <= moment <= ends:
            return f"{name} está cerrado temporalmente."

    hours = branch.get("merchant_branch_hours") or []
    if not hours:
        # Sin horario cargado manda el interruptor de abierto/cerrado.
        return None
    minute_of_day = moment.hour * 60 + moment.minute
    today = _row_for(hours, _dow(moment))
    yesterday = _row_for(hours, (_dow(moment) + 6) % 7)
    if _within(today, minute_of_day) or _within(yesterday, minute_of_day, previous_day=True):
        return None

    message = f"{name} está cerrado ahora."
    next_opening = _next_opening(hours, moment)
    return f"{message} {next_opening}" if next_opening else message
