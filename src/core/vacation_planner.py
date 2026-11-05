"""Decision logic for the daily rolling vacation-mode safeguard.

Travian Kingdoms starts vacation mode 24 hours after it is planned and lets
the player cancel it (refunding the vacation days) until then. The safeguard
plans vacation every day at a fixed time. Shortly before activation it
cancels the plan unless enough hostile movements are incoming; the next
planning window then schedules a fresh one. Under attack the plan is left
alone and vacation activates, so attackers find the player on vacation.

This module is pure: it never talks to the game, which keeps every branch
testable. The caller executes the returned action and verifies the outcome.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

VACATION_STATE_NONE = 0
VACATION_STATE_PLANNED = 1

# Never send a cancellation this close to activation: the request could race
# the server switching vacation on.
CANCEL_SAFETY_SECONDS = 60

ACTION_PLAN = "plan"
ACTION_CANCEL = "cancel"
ACTION_KEEP = "keep"
ACTION_WAIT = "wait"
ACTION_NONE = "none"


@dataclass(frozen=True)
class VacationDecision:
    action: str
    reason: str
    counted_attacks: int = 0
    next_check: datetime | None = None


def parse_plan_time(value: str) -> tuple[int, int]:
    hours, minutes = value.split(":")
    return int(hours), int(minutes)


def count_hostile_movements(
    attacks: list[dict] | None,
    counted_types: set[str],
    server_time: float,
) -> int:
    """Count incoming movements of the configured types still on their way."""
    return sum(
        1
        for attack in attacks or []
        if attack.get("movement_type") in counted_types
        and float(attack.get("arrival_time") or 0) > server_time
    )


def plan_window(
    now_local: datetime,
    plan_time: str,
    window_minutes: int,
) -> tuple[datetime, datetime]:
    """Return today's local planning window."""
    hours, minutes = parse_plan_time(plan_time)
    start = now_local.replace(hour=hours, minute=minutes, second=0, microsecond=0)
    return start, start + timedelta(minutes=window_minutes)


def decide_vacation_action(
    state: dict,
    now_local: datetime,
    attacks: list[dict] | None,
    *,
    plan_time: str,
    plan_window_minutes: int,
    decision_minutes_before: int,
    attack_threshold: int,
    counted_movement_types: set[str],
    last_plan_date: str | None,
) -> VacationDecision:
    """Return what the safeguard should do right now.

    *state* holds ``vacation_state``, ``start``, ``end`` and ``server_time``
    (Unix seconds, as reported by the game). *attacks* may be ``None`` when
    they were not read; the decision then never cancels.
    """
    vacation_state = state["vacation_state"]
    server_time = float(state["server_time"])
    start = float(state.get("start") or 0)
    end = float(state.get("end") or 0)

    if vacation_state == VACATION_STATE_NONE:
        window_start, window_end = plan_window(
            now_local,
            plan_time,
            plan_window_minutes,
        )
        today = now_local.date().isoformat()
        if last_plan_date == today:
            tomorrow = window_start + timedelta(days=1)
            return VacationDecision(
                ACTION_WAIT,
                "Dzisiejsze wakacje już zaplanowano; kolejne planowanie "
                f"{tomorrow:%d.%m %H:%M}.",
                next_check=tomorrow,
            )
        if window_start <= now_local < window_end:
            return VacationDecision(
                ACTION_PLAN,
                "Okno planowania — planuję wakacje.",
            )
        next_window = (
            window_start if now_local < window_start
            else window_start + timedelta(days=1)
        )
        return VacationDecision(
            ACTION_WAIT,
            f"Brak zaplanowanych wakacji; planowanie {next_window:%d.%m %H:%M}.",
            next_check=next_window,
        )

    if vacation_state != VACATION_STATE_PLANNED or server_time >= start:
        ends = (
            f" do {datetime.fromtimestamp(end):%d.%m %H:%M}" if end > 0 else ""
        )
        return VacationDecision(
            ACTION_NONE,
            f"Wakacje są aktywne{ends}. Automat nic nie zmienia.",
        )

    seconds_to_start = start - server_time
    decision_seconds = decision_minutes_before * 60
    activation = datetime.fromtimestamp(start)
    if seconds_to_start > decision_seconds:
        decision_at = activation - timedelta(seconds=decision_seconds)
        return VacationDecision(
            ACTION_WAIT,
            f"Wakacje zaplanowane na {activation:%d.%m %H:%M}; decyzja "
            f"o {decision_at:%H:%M}.",
            next_check=decision_at,
        )

    if attacks is None:
        return VacationDecision(
            ACTION_KEEP,
            "Nie udało się odczytać ataków — zostawiam wakacje "
            "dla bezpieczeństwa.",
        )

    counted = count_hostile_movements(
        attacks,
        counted_movement_types,
        server_time,
    )
    if counted >= attack_threshold:
        return VacationDecision(
            ACTION_KEEP,
            f"{counted} wrogich ruchów (próg {attack_threshold}) — wakacje "
            f"włączą się o {activation:%H:%M}.",
            counted,
        )
    if seconds_to_start <= CANCEL_SAFETY_SECONDS:
        return VacationDecision(
            ACTION_KEEP,
            "Za mało czasu na bezpieczne anulowanie — wakacje się włączą.",
            counted,
        )
    return VacationDecision(
        ACTION_CANCEL,
        f"{counted} wrogich ruchów (próg {attack_threshold}) — anuluję "
        "wakacje; zaplanuję je ponownie w oknie planowania.",
        counted,
    )
