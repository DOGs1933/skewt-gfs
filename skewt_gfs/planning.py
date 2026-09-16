from datetime import datetime, timedelta, timezone

UTC = timezone.utc


def parse_time(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("La fecha debe incluir zona horaria: 2026-09-16T12:00:00Z")
    return result.astimezone(UTC)


def iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def floor_cycle(now: datetime) -> datetime:
    now = now.astimezone(UTC)
    return now.replace(hour=now.hour // 6 * 6, minute=0, second=0, microsecond=0)


def candidate_cycles(now: datetime, max_age: int):
    cycle = floor_cycle(now)
    while now - cycle <= timedelta(hours=max_age):
        yield cycle
        cycle -= timedelta(hours=6)


def target_hours(now: datetime, horizon: int, step: int):
    # First whole UTC hour at or after the requested instant; never label an old hour as "now".
    first = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    if first < now:
        first += timedelta(hours=1)
    return [first + timedelta(hours=h) for h in range(0, horizon + 1, step)]


def forecast_hours(cycle: datetime, targets: list[datetime]):
    result = []
    for target in targets:
        delta = (target - cycle).total_seconds() / 3600
        if delta != int(delta) or not 0 <= delta <= 120:
            raise ValueError("El plazo debe ser una hora entera entre 0 y 120")
        result.append(int(delta))
    return result
