"""Village building automation: read, plan, then click like a player.

Every tick reads all villages, plans them and (when the automation is on)
for each village, in this order:

1. finishes the running build instantly (free finishes in every mode but
   "off"; paid ones only in ``auto``) when its remaining time is inside
   an allowed price window and it is useful (always when free; when paid
   only if the next step's resources are waiting) — "finish" mode ``auto``.
   Outside the windows it waits (e.g. 2–3 h left: until it costs 1 gold;
   too short for paying: until it is free); ``next_wakeup`` says when;
2. clicks the next planned upgrade when it is affordable and the queue free;
3. otherwise uses the NPC trader when that makes the next step affordable
   now and saves at least the configured time — "npc" mode ``auto`` —
   and then clicks the upgrade.

Paid actions never take the gold balance below the configured reserve.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

try:
    from src.core import village_builder as vb
    from src.core.attack_clicker import ClickModeError
    from src.core.build_clicker import (
        BuildClickExecutor, GoldGuardError, StaleStateError, read_build_state,
    )
except ModuleNotFoundError:  # pragma: no cover - script fallback
    from core import village_builder as vb
    from core.attack_clicker import ClickModeError
    from core.build_clicker import (
        BuildClickExecutor, GoldGuardError, StaleStateError, read_build_state,
    )

logger = logging.getLogger(__name__)

# After a failed click the village is left alone for a while.
FAILURE_BACKOFF_SECONDS = 600
# Outdated read (the game already shows a newer level): retry soon.
STALE_BACKOFF_SECONDS = 30
# Let the game models catch up after a finished build before re-planning.
SETTLE_SECONDS = 4.0
# Pause between two clicked actions (a player does not click instantly).
CLICK_PAUSE_SECONDS = 3.0
DEFAULT_NPC_PRICE = 5
# A village sends at most one shipment in this time.
TRANSFER_COOLDOWN_SECONDS = 5 * 60
# A master builder location is not re-added/cancelled again within this time.
MASTER_CHURN_SECONDS = 30 * 60


@dataclass
class BuildSnapshot:
    plans: list[vb.VillagePlan] = field(default_factory=list)
    transfers: list[vb.Transfer] = field(default_factory=list)
    finish: dict[str, list[vb.FinishOffer]] = field(default_factory=dict)
    npc: dict[str, float | None] = field(default_factory=dict)  # village -> hours saved
    server_time: float = 0.0
    read_at: float = 0.0
    missing: list[str] = field(default_factory=list)
    gold: int = 0
    npc_price: int = DEFAULT_NPC_PRICE
    # Seconds until a running build enters an allowed finish window.
    next_wakeup: float | None = None
    # Gold budget plan (None when no budget is set).
    gold_plan: vb.GoldAllocation | None = None
    budget_left: int | None = None
    network: vb.NetworkBalance | None = None
    troops: dict[int, dict] = field(default_factory=dict)  # unit -> costs, building_types
    hero: dict | None = None  # village_id, type (0 all, 1-4), points, dead
    # Village id -> server time the first hostile (non-scout) attack lands.
    threats: dict[str, float] = field(default_factory=dict)
    merchant_speed: dict[int, float] = field(default_factory=dict)
    world_speed: float = 1.0
    crop_bank: str | None = None  # village id of the crop bank (setting on)


# Hero production: re-check the best setting this often, and keep a new
# setting at least this long (no flip-flopping).
HERO_CHECK_SECONDS = 600
HERO_SWITCH_SECONDS = 2 * 3600
# Per-resource hero production of "all resources" per point, until read.
HERO_ALL_PER_POINT = 1080 / 68
HERO_NAMES = {0: "wszystkie", 1: "drewno", 2: "glina", 3: "żelazo", 4: "zboże"}
# Switches for a stalled queue: at most one per this many seconds.
HERO_STALL_SECONDS = 10 * 60
# Shipments are not planned to a village an attack hits within this long
# after they would arrive (the loot would just go to the attacker).
RAID_SHIPMENT_WINDOW_HOURS = 2.0
# While an attack is about to land the villages are checked this often.
RAID_RECHECK_SECONDS = 60


def hostile_arrivals(attacks, server_time: float) -> dict[str, float]:
    """Village id -> earliest landing of an attack that can loot (scouting
    does not take resources)."""
    result: dict[str, float] = {}
    for attack in attacks or []:
        if str(attack.get("movement_type") or "") == "Zwiad":
            continue
        try:
            arrival = float(attack.get("arrival_time") or 0)
        except (TypeError, ValueError):
            continue
        village_id = str(attack.get("village_id") or "")
        if not village_id or arrival <= server_time:
            continue
        result[village_id] = min(result.get(village_id, arrival), arrival)
    return result


def finish_tiers(settings) -> vb.FinishTiers:
    return vb.FinishTiers(
        free_seconds=settings.finish_free_minutes * 60,
        reduced_seconds=settings.finish_reduced_minutes * 60,
        reduced_price=settings.finish_reduced_price,
        full_price=settings.finish_full_price,
        allow_free=settings.finish_free,
        allow_reduced=settings.finish_reduced,
        allow_full=settings.finish_full,
        reduced_min_seconds=settings.finish_reduced_min_minutes * 60,
        full_min_seconds=settings.finish_full_min_minutes * 60,
    )


def npc_saving(plan: vb.VillagePlan, min_saved_minutes: int) -> float | None | bool:
    """Hours an NPC trade saves for step 1, or False when it is not worth it."""
    if not plan.npc_ready_now:
        return False
    saved = plan.afford_hours  # None = would never accrue without trading
    if saved is not None and saved * 60 < min_saved_minutes:
        return False
    return saved


def budget_offers(
    plan: vb.VillagePlan,
    tiers: vb.FinishTiers,
    server_time: float,
    gold_plan: vb.GoldAllocation,
    budget_left: int,
) -> list[vb.FinishOffer]:
    """Finish offers under a gold budget.

    Free finishes as always. A paid one only when the next step's resources
    are waiting and the build is worth at least the budget's threshold in
    queue hours per gold; then either pay the full price now or wait until
    the reduced price, whichever saves more hours per gold.
    """
    offers = []
    for offer in vb.finish_offers(plan, tiers, server_time):
        remaining = offer.remaining_seconds
        if remaining < tiers.free_seconds:
            if tiers.allow_free:
                offers.append(offer)
            continue
        value, price, now = vb.finish_value(remaining, tiers)
        waiting = plan.next_step is not None and (
            (plan.afford_hours is not None and plan.afford_hours <= 0)
            or (plan.afford_hours_npc is not None and plan.afford_hours_npc <= 0)
        )
        if price <= 0 or price > budget_left or value < gold_plan.threshold or not waiting:
            # Not worth gold: just wait for the free finish.
            if tiers.allow_free and remaining >= tiers.free_seconds:
                offer.allowed = False
                offer.wait_seconds = remaining - tiers.free_seconds + 1
                offer.next_price = 0
                offer.useful = True
                offer.reason = f"czeka {offer.wait_seconds / 60:.0f} min na darmowe dokończenie"
                offers.append(offer)
            continue
        offer.useful = True
        offer.reason = f"budżet złota: {value:.1f} h kolejki na 1 złoto"
        if now and offer.price == price:
            offer.allowed = True
            offer.wait_seconds = 0.0
            offer.next_price = price
        else:
            offer.allowed = False
            offer.wait_seconds = remaining - tiers.reduced_seconds + 1
            offer.next_price = price
            offer.reason += f"; czeka do ceny {price} złota"
        offers.append(offer)
    return offers


class BuildAutomation:
    def __init__(self, session, settings_getter, log, executor=None, recruitment_getter=None,
                 threats_getter=None):
        self._session = session
        # Incoming attacks from the app's live listener (list of dicts).
        self._threats = threats_getter or (lambda: [])
        # (village id, landing time) already reported as "nothing to save".
        self._raid_noted: set[tuple[str, float]] = set()
        self._crop_bank: str | None = None
        self._first_steps: dict[str, list] = {}
        self._bank_gold_noted = False
        # village id -> {"wood"|"clay"|"iron"|"grain": cost per hour or None}
        self._recruitment = recruitment_getter or (lambda: {})
        self._settings = settings_getter

        def _log(text: str) -> None:
            logger.info("Village build: %s", text)
            log(text)

        self._log = _log
        self._executor = executor or BuildClickExecutor(session)
        self._backoff: dict[str, float] = {}
        self._sent_at: dict[str, float] = {}
        self._construct_config: dict[int, dict] = {}
        # (village id, building type) -> server time its training queue ends.
        self._training_until: dict[tuple[str, int], float] = {}
        self._military_active: list[dict] = []
        self._hero_amount_all = 0.0
        self._hero_next_check = 0.0
        # Set while the hero produces for a stalled queue (not the long-term
        # best): it goes back as soon as the stall is over.
        self._hero_for_stall = False
        self._hero_stall_next = 0.0
        self._military_targets: dict[str, dict[int, int]] = {}
        self._military_first: set[str] = set()
        # village -> {("added"|"cancelled", location): monotonic time}
        self._recent_master: dict[str, dict] = {}
        self.snapshot = BuildSnapshot()
        # Set when gold went missing: no more clicking until restarted.
        self.halted: str | None = None
        # Gold was spent from the budget: the app saves the settings.
        self.budget_changed = False
        # Set by the app when this instance hung and was replaced: it must
        # not click anything any more, even if its call returns later.
        self.abandoned = False

    def refresh(self) -> BuildSnapshot:
        """Read the game and compute the plan and recommendations (no clicking)."""
        settings = self._settings()
        state = read_build_state(self._session)
        villages = []
        missing = []
        for raw in state["villages"]:
            if raw.get("missing"):
                missing.append(str(raw.get("name") or raw.get("id")))
                continue
            try:
                villages.append(vb.village_from_raw(raw))
            except (KeyError, TypeError, ValueError) as error:
                logger.warning("Skipping village %s: %s", raw.get("id"), error)
                missing.append(str(raw.get("name") or raw.get("id")))
        server_time = float(state.get("server_time") or time.time())
        # Base data of buildings that can be built new (bonus, marketplace).
        self._construct_config = {
            int(k): v for k, v in (state.get("building_config") or {}).items()
        }
        # Continuous recruitment eats resources: reserve them for it.
        names = {"wood": 1, "clay": 2, "iron": 3, "grain": 4}
        rates = self._recruitment() or {}
        unknown = []
        for village in villages:
            costs = rates.get(village.id) or {}
            if any(value is None for value in costs.values()):
                unknown.append(village.name)
                continue
            village.recruitment = {
                names[name]: float(value) for name, value in costs.items()
                if name in names and value
            }
            village.recruitment_reserve = {
                r: rate * settings.recruitment_reserve_hours
                for r, rate in village.recruitment.items()
            }
        missing.extend(f"{name} (koszt rekrutacji nieznany)" for name in unknown)
        # Carry per merchant is the same for all own villages of a tribe;
        # fill it in where the game has not loaded it yet.
        carry = max((v.merchant_carry for v in villages), default=0)
        for village in villages:
            if village.marketplace is not None and village.merchant_carry <= 0:
                village.merchant_carry = carry
        military_targets, military_first, self._military_active = self._military_plan(
            villages, settings, server_time,
        )
        self._military_targets, self._military_first = military_targets, military_first
        self._crop_bank = (
            vb.crop_bank_village(villages, settings.crop_bank_village) if settings.crop_bank else None
        )
        # A hero switched to one resource (e.g. for a stalled queue) must not
        # make the storage look too small for "production" it soon stops.
        hero = state.get("hero") or {}
        if hero.get("points") and not hero.get("dead"):
            amount_all = self._hero_amount_all or float(hero["points"]) * HERO_ALL_PER_POINT
            current = vb.hero_contribution(int(hero.get("type") or 0), amount_all)
            steady = vb.hero_contribution(vb.HERO_ALL, amount_all)
            for village in villages:
                if village.id == str(hero.get("village_id")):
                    village.hero_excess = {r: current[r] - steady[r] for r in vb.RESOURCES}
        plans = vb.plan_all(
            villages,
            settings.capital_target,
            settings.village_target,
            server_time,
            main_building=settings.main_building,
            # With the NPC trader on "auto" any surplus is converted: 1:1.
            balanced=settings.balance_resources and settings.npc_mode != "auto",
            storage_hours=settings.storage_hours,
            bonus_buildings=settings.bonus_buildings,
            construct_config=self._construct_config,
            military=military_targets,
            military_first=military_first,
            granary_max={self._crop_bank} if self._crop_bank and settings.crop_bank_granaries else None,
        )
        merchant_speed = {}
        for key, value in (state.get("merchant_speed") or {}).items():
            try:
                merchant_speed[int(key)] = float(value)
            except (TypeError, ValueError):
                continue
        network = vb.network_balance(
            plans, merchant_speed, float(state.get("world_speed") or 1),
        )
        # Steps put in front of a village's own plan (marketplace): the master
        # builder, which re-plans the village itself, must line them up first.
        first_steps: dict[str, list[vb.Step]] = {}
        self._first_steps = first_steps
        # A village with resources to move out (network surplus) or close to
        # overflowing gets a marketplace first, if it has none.
        for plan in plans:
            village = plan.village
            if village.marketplace is not None or not village.empty_slots:
                continue
            overflow_soon = any(
                value > 0 for value in vb.overflow_excess(plan, 4.0).values()
            )
            if village.id in network.surplus or overflow_soon:
                step = vb.marketplace_construction(
                    village, self._construct_config,
                    "nowe targowisko: wioska ma nadwyżkę, a nie może jej wysłać",
                )
                if step is not None and not any(s.construct and s.type == vb.MARKETPLACE for s in plan.steps):
                    plan.steps.insert(0, step)
                    first_steps.setdefault(village.id, []).insert(0, step)
                    plan.afford_hours = vb.time_to_afford(
                        step.costs, village.build_resources(), village.build_production(),
                    )
                    plan.afford_hours_npc = vb.time_to_afford_total(
                        step.costs, village.build_resources(), village.build_production(),
                    )
        # Crop bank: each village's marketplace carries its spare crop and
        # crop production to the bank within a few hours.
        bank_village = next((p.village for p in plans if p.village.id == self._crop_bank), None)
        if bank_village is not None:
            for plan in plans:
                village = plan.village
                if village.id == bank_village.id or village.marketplace is None:
                    continue
                missing = vb.bank_merchants_missing(
                    plan, bank_village, merchant_speed, float(state.get("world_speed") or 1),
                )
                if missing > network.extra_merchants.get(village.id, 0):
                    network.extra_merchants[village.id] = missing
        # Merchants cannot carry a village's share of the useful surplus:
        # upgrade its marketplace first (one merchant per level).
        for plan in plans:
            missing = network.extra_merchants.get(plan.village.id, 0)
            step = vb.marketplace_step(plan.village, missing)
            if step is not None and not any(s.type == vb.MARKETPLACE for s in plan.steps):
                plan.steps.insert(0, step)
                first_steps.setdefault(plan.village.id, []).insert(0, step)
                plan.afford_hours = vb.time_to_afford(
                    step.costs, plan.village.build_resources(), plan.village.build_production(),
                )
                plan.afford_hours_npc = vb.time_to_afford_total(
                    step.costs, plan.village.build_resources(), plan.village.build_production(),
                )
        transfers = [] if settings.transfer_mode == "off" else vb.plan_transfers(
            plans,
            merchant_speed,
            float(state.get("world_speed") or 1),
            settings.transfer_min_saved_minutes,
            min_surplus_shipment=settings.transfer_min_amount,
            crop_bank=self._crop_bank,
        )
        tiers = finish_tiers(settings)
        gold_plan = None
        budget_left = None
        if settings.gold_budget > 0:
            budget_left = max(0, settings.gold_budget - settings.gold_budget_spent)
            all_tiers = vb.FinishTiers(
                free_seconds=tiers.free_seconds, reduced_seconds=tiers.reduced_seconds,
                reduced_price=tiers.reduced_price, full_price=tiers.full_price,
                allow_free=True, allow_reduced=True, allow_full=True,
            )
            gold_plan = vb.plan_gold_budget(plans, budget_left, all_tiers)
        finish = {}
        npc = {}
        waits = []
        threats = hostile_arrivals(self._threats(), server_time) if settings.raid_protection else {}
        if threats:
            # Shipments into a village about to be raided feed the attacker.
            transfers = [
                t for t in transfers
                if not vb.shipment_raided(
                    threats, t.target_id, server_time, t.travel_hours, RAID_SHIPMENT_WINDOW_HOURS,
                )
            ]
            for landing in threats.values():
                start = landing - settings.raid_lead_minutes * 60 - server_time
                if start > 0:
                    waits.append(start + 5)
                else:
                    # Inside the window: check again as soon as merchants may
                    # be back for another trip.
                    waits.append(RAID_RECHECK_SECONDS)
        for plan in plans:
            if settings.finish_mode != "off":
                if gold_plan is not None:
                    offers = budget_offers(plan, tiers, server_time, gold_plan, budget_left)
                else:
                    offers = [
                        offer for offer in vb.finish_offers(plan, tiers, server_time)
                        if offer.useful and (offer.allowed or offer.wait_seconds)
                    ]
                if offers:
                    finish[plan.village.id] = offers
                waits.extend(o.wait_seconds for o in offers if o.wait_seconds)
            # A warehouse/granary in progress while storage overflows: finishing
            # it with gold saves the resources (when worth it per gold).
            if settings.finish_mode != "off" and settings.overflow_finish_per_gold > 0:
                shipped = {r: 0.0 for r in vb.RESOURCES}
                for transfer in transfers:
                    if transfer.source_id == plan.village.id:
                        for r, amount in transfer.resources.items():
                            shipped[r] += amount
                chain = vb.storage_chain(plan, server_time, tiers, shipped)
                if (
                    chain is not None
                    and plan.village.id == self._crop_bank
                    and chain.storage_type in vb.GRANARY_TYPES
                ):
                    chain = None  # the crop bank mills its crop instead of paying to keep it
                if chain is not None:
                    entry = chain.first
                    remaining = entry.finished - server_time
                    price = tiers.price(remaining)
                    affordable = budget_left is None or chain.price <= budget_left
                    worth = chain.price == 0 or chain.saved >= settings.overflow_finish_per_gold * chain.price
                    if affordable and worth:
                        storage = "spichlerz" if chain.storage_type in vb.GRANARY_TYPES else "magazyn"
                        offers = [o for o in finish.get(plan.village.id, []) if o.queue_type != entry.queue_type]
                        offers.append(vb.FinishOffer(
                            village_id=plan.village.id,
                            queue_type=entry.queue_type,
                            location=entry.location,
                            building_type=entry.building_type,
                            remaining_seconds=remaining,
                            price=price,
                            allowed=True,
                            useful=True,
                            reason=(
                                f"{storage} się przepełni — dokończenie {chain.steps} "
                                f"bud. za {chain.price} zł ratuje ~{chain.saved:,.0f} surowców"
                            ).replace(",", " "),
                        ))
                        finish[plan.village.id] = offers
            # Wake up when a step becomes affordable (production or loot).
            ready = plan.ready_hours
            if plan.next_step is not None and ready is not None and 0 < ready < 1:
                waits.append(ready * 3600 + 5)
            if settings.npc_mode != "off":
                saved = npc_saving(plan, settings.npc_min_saved_minutes)
                if saved is not False:
                    npc[plan.village.id] = saved
        self.snapshot = BuildSnapshot(
            plans=plans,
            transfers=transfers,
            finish=finish,
            npc=npc,
            server_time=server_time,
            read_at=time.time(),
            missing=missing,
            gold=int(state.get("gold") or 0),
            npc_price=int(state.get("npc_price") or DEFAULT_NPC_PRICE),
            next_wakeup=min(waits) if waits else None,
            gold_plan=gold_plan,
            budget_left=budget_left,
            network=network,
            hero=state.get("hero"),
            threats=threats,
            merchant_speed=merchant_speed,
            world_speed=float(state.get("world_speed") or 1),
            crop_bank=self._crop_bank,
            troops={
                int(k): {
                    "costs": {int(r): float(v) for r, v in (info.get("costs") or {}).items()},
                    "building_types": [int(t) for t in info.get("building_types") or []],
                }
                for k, info in (state.get("troops") or {}).items()
            },
        )
        return self.snapshot

    def tick(self) -> BuildSnapshot:
        """Refresh the plan and, when enabled, act in every village."""
        snapshot = self.refresh()
        settings = self._settings()
        if not settings.enabled or self.halted:
            return snapshot
        acted = self._act(snapshot, settings)
        if not self.halted and self._train(snapshot, settings):
            acted = True
        if not self.halted and settings.hero_production_auto and self._hero(snapshot):
            acted = True
        imminent = {
            village_id: landing for village_id, landing in snapshot.threats.items()
            if landing - snapshot.server_time <= settings.raid_lead_minutes * 60
        }
        if not self.halted and imminent:
            if acted:
                # Building/reserving/training changed the stock: read it again.
                time.sleep(SETTLE_SECONDS)
                snapshot = self.refresh()
            if self._protect(snapshot, settings, imminent):
                acted = True
                time.sleep(SETTLE_SECONDS)
                snapshot = self.refresh()
        if not self.halted and settings.crop_bank and self._mill(snapshot, settings):
            acted = True
            time.sleep(SETTLE_SECONDS)
            snapshot = self.refresh()
        if not self.halted and settings.transfer_mode == "auto" and self._send(snapshot, settings):
            acted = True
        if not self.halted and (settings.overflow_units or settings.overflow_auto) and self._burn_overflow(snapshot, settings):
            acted = True
        if acted:
            # A finished build frees the queue: try once more right away,
            # after the game had a moment to update its building data.
            time.sleep(SETTLE_SECONDS)
            snapshot = self.refresh()
            if not self.halted and self._act(snapshot, settings, finish=False):
                snapshot = self.refresh()
        return snapshot

    def _act(self, snapshot: BuildSnapshot, settings, finish: bool = True) -> bool:
        now = time.monotonic()
        gold = snapshot.gold
        acted = False
        for plan in snapshot.plans:
            village = plan.village
            if self.abandoned:
                return acted
            if village.id in settings.disabled_villages or self._backoff.get(village.id, 0) > now:
                continue
            finished_here = False
            try:
                if finish and settings.finish_mode != "off":
                    for offer in snapshot.finish.get(village.id, []):
                        if not offer.allowed or offer.wait_seconds:
                            continue
                        # "Recommend" only holds back gold; free finishes run.
                        if offer.price > 0 and settings.finish_mode != "auto":
                            continue
                        if offer.price > 0 and gold - offer.price < settings.gold_reserve:
                            continue
                        if offer.price > 0 and settings.gold_budget > 0 and (
                            offer.price > settings.gold_budget - settings.gold_budget_spent
                        ):
                            continue
                        if acted:
                            time.sleep(CLICK_PAUSE_SECONDS)
                        if self.abandoned:
                            return acted
                        logger.info("Village build action: %s finish now (%s gold)", village.name, offer.price)
                        paid = self._executor.finish_now(village.id, offer.queue_type, offer.price)
                        gold -= paid
                        settings.gold_budget_spent += paid
                        self.budget_changed = self.budget_changed or paid > 0
                        acted = True
                        self._log(
                            f"{village.name}: budowa dokończona "
                            + ("za darmo" if paid == 0 else f"za {paid} złota")
                            + f" ({offer.reason})."
                        )
                        finished_here = True
                if finished_here:
                    # The queue just changed: act on a fresh read (next pass).
                    continue
                # Resources in stock for the next master builder entry: reserve
                # them now (they leave the storage and cannot overflow).
                if settings.master_builder_slots > 0:
                    reservable = vb.reservable_master_entry(village)
                    if reservable is not None:
                        entry, _costs = reservable
                        if acted:
                            time.sleep(CLICK_PAUSE_SECONDS)
                        if self.abandoned:
                            return acted
                        logger.info("Village build action: %s reserve master %s", village.name, entry.id)
                        result = self._executor.reserve_master(village.id, entry.id)
                        if result == "reserved":
                            acted = True
                            name = (vb.FIELD_NAMES.get(entry.building_type)
                                    or vb.BUILDING_NAMES.get(entry.building_type, f"budynek {entry.building_type}"))
                            self._log(f"{village.name}: zarezerwowano surowce na {name} u budowniczego.")
                            continue  # act on a fresh read
                if settings.master_builder_slots > 0 and settings.master_replan:
                    stale = vb.master_replacements(
                        village, plan.target, snapshot.server_time,
                        settings.master_builder_slots,
                        main_building=settings.main_building,
                        balanced=settings.balance_resources and settings.npc_mode != "auto",
                        storage_hours=settings.storage_hours,
                        bonus_buildings=settings.bonus_buildings,
                        construct_config=self._construct_config,
                        military=self._military_targets.get(village.id),
                        military_first=village.id in self._military_first,
                        granary_max=village.id == snapshot.crop_bank and settings.crop_bank_granaries,
                        first_steps=self._first_steps.get(village.id),
                    )
                    recent = self._recent_master.get(village.id, {})
                    now_m = time.monotonic()
                    stale = [
                        e for e in stale
                        if now_m - recent.get(("added", e.location), -1e9) > MASTER_CHURN_SECONDS
                    ]
                    if stale:
                        entry = stale[0]
                        name = (vb.FIELD_NAMES.get(entry.building_type)
                                or vb.BUILDING_NAMES.get(entry.building_type, f"budynek {entry.building_type}"))
                        if acted:
                            time.sleep(CLICK_PAUSE_SECONDS)
                        if self.abandoned:
                            return acted
                        logger.info("Village build action: %s cancel master %s", village.name, entry.id)
                        self._executor.cancel_master(village.id, entry.id)
                        self._recent_master.setdefault(village.id, {})[("cancelled", entry.location)] = time.monotonic()
                        acted = True
                        self._log(
                            f"{village.name}: z kolejki budowniczego usunięto {name} "
                            f"(miejsce {entry.location}, bez rezerwacji) — plan ma lepszy krok."
                        )
                        continue  # fill the freed slot on a fresh read
                if settings.master_builder_slots > 0 and settings.master_replan:
                    stalled = vb.stalled_master_entry(plan)
                    recent = self._recent_master.get(village.id, {})
                    if (
                        stalled is not None
                        and not any(t.target_id == village.id for t in snapshot.transfers)
                        and time.monotonic() - recent.get(("added", stalled[0].location), -1e9)
                        > MASTER_CHURN_SECONDS
                    ):
                        entry, hours = stalled
                        name = (vb.FIELD_NAMES.get(entry.building_type)
                                or vb.BUILDING_NAMES.get(entry.building_type, f"budynek {entry.building_type}"))
                        if acted:
                            time.sleep(CLICK_PAUSE_SECONDS)
                        if self.abandoned:
                            return acted
                        logger.info("Village build action: %s cancel stalled master %s", village.name, entry.id)
                        self._executor.cancel_master(village.id, entry.id)
                        self._recent_master.setdefault(village.id, {})[("cancelled", entry.location)] = time.monotonic()
                        acted = True
                        self._log(
                            f"{village.name}: z kolejki budowniczego usunięto {name} — brakuje na niego "
                            f"surowców na ~{hours * 60:.0f} min, a coś innego może ruszyć od razu "
                            f"(wróci do kolejki, gdy plan do niego dojdzie)."
                        )
                        continue  # fill the queue on a fresh read
                step = plan.next_step
                if step is not None and not plan.queue_free:
                    # Queue busy: line up the next planned step with the
                    # master builder so it starts the moment the queue frees.
                    in_master = sum(1 for entry in village.queue if entry.queue_type == 4)
                    recent = self._recent_master.get(village.id, {})
                    now_m = time.monotonic()
                    avoid = {
                        location for (kind, location), at in recent.items()
                        if kind == "cancelled" and now_m - at < MASTER_CHURN_SECONDS
                    }
                    master_step = vb.master_next_step(
                        village, plan.target, snapshot.server_time,
                        settings.master_builder_slots, avoid,
                        main_building=settings.main_building,
                        balanced=settings.balance_resources and settings.npc_mode != "auto",
                        storage_hours=settings.storage_hours,
                        bonus_buildings=settings.bonus_buildings,
                        construct_config=self._construct_config,
                        military=self._military_targets.get(village.id),
                        military_first=village.id in self._military_first,
                        granary_max=village.id == snapshot.crop_bank and settings.crop_bank_granaries,
                        first_steps=self._first_steps.get(village.id),
                    )
                    if master_step is not None:
                        step = master_step
                    if (
                        master_step is not None
                        and settings.master_builder_slots > in_master
                        and village.free_slots.get(4, 0) > 0
                    ):
                        label = f"{village.name}: {step.name} {step.from_level}→{step.from_level + 1}"
                        if acted:
                            time.sleep(CLICK_PAUSE_SECONDS)
                        if self.abandoned:
                            return acted
                        logger.info("Village build action: %s master builder", label)
                        if step.construct:
                            # New building: the construction window queues it.
                            result = self._executor.construct(village.id, step.location, step.type)
                        else:
                            building = vb._building(village, step.location)
                            result = self._executor.queue_master(
                                village.id, step.location, building.level, step.is_field,
                            )
                        acted = True
                        self._recent_master.setdefault(village.id, {})[("added", step.location)] = time.monotonic()
                        self._log(
                            f"{label} — dodane do kolejki budowniczego"
                            + (", surowce zarezerwowane" if result == "reserved"
                               else " (ruszy, gdy będą surowce)")
                            + f" ({step.reason})."
                        )
                    continue
                if step is None:
                    continue
                label = f"{village.name}: {step.name} {step.from_level}→{step.from_level + 1}"
                if not plan.ready_now:
                    if (
                        settings.npc_mode != "auto"
                        or village.id not in snapshot.npc
                        or gold - snapshot.npc_price < settings.gold_reserve
                        or (settings.gold_budget > 0 and snapshot.npc_price
                            > settings.gold_budget - settings.gold_budget_spent)
                    ):
                        continue
                    if acted:
                        time.sleep(CLICK_PAUSE_SECONDS)
                    if self.abandoned:
                        return acted
                    logger.info("Village build action: %s NPC trade", village.name)
                    result = self._executor.npc_trade(
                        village.id, step.location, step.from_level, step.is_field,
                        step.costs, snapshot.npc_price,
                    )
                    acted = True
                    if result.startswith("traded"):
                        spent = int(result.split(":")[1] or 0)
                        gold -= spent
                        settings.gold_budget_spent += spent
                        self.budget_changed = self.budget_changed or spent > 0
                        saved = snapshot.npc.get(village.id)
                        self._log(
                            f"{label} — wymiana u handlarza NPC za {spent} złota "
                            + ("(bez niej surowce by nie narosły)." if saved is None
                               else f"(oszczędza {saved:.1f} h).")
                        )
                if acted:
                    time.sleep(CLICK_PAUSE_SECONDS)
                if self.abandoned:
                    return acted
                logger.info("Village build action: %s upgrade", label)
                if step.construct:
                    self._executor.construct(village.id, step.location, step.type)
                    acted = True
                    self._backoff.pop(village.id, None)
                    self._log(f"{village.name}: {step.name} — budowa nowego budynku rozpoczęta ({step.reason}).")
                    continue
                self._executor.upgrade(village.id, step.location, step.from_level, step.is_field)
                acted = True
                self._backoff.pop(village.id, None)
                self._log(f"{label} — rozbudowa rozpoczęta ({step.reason}).")
            except GoldGuardError as error:
                self.halted = str(error)
                self._log(f"{village.name}: STOP — {error}")
                return acted
            except StaleStateError as error:
                self._backoff[village.id] = now + STALE_BACKOFF_SECONDS
                self._log(f"{village.name}: dane gry jeszcze się odświeżały ({error}) — ponowię za chwilę.")
            except ClickModeError as error:
                self._backoff[village.id] = now + FAILURE_BACKOFF_SECONDS
                self._log(f"{village.name}: nie wykonano — {error}")
            except Exception as error:  # browser/tab problems
                logger.exception("Build click failed")
                self._backoff[village.id] = now + FAILURE_BACKOFF_SECONDS
                self._log(f"{village.name}: błąd przeglądarki — {error}")
                self._executor.close()
        return acted

    def _send(self, snapshot: BuildSnapshot, settings) -> bool:
        """Send the recommended shipments (one per source village and tick)."""
        now = time.monotonic()
        villages = {plan.village.id: plan.village for plan in snapshot.plans}
        sent = False
        used_sources = set()
        for transfer in snapshot.transfers:
            source = villages.get(transfer.source_id)
            target = villages.get(transfer.target_id)
            if (
                source is None or target is None or source.marketplace is None
                or transfer.source_id in used_sources
                or transfer.source_id in settings.disabled_villages
                or self._sent_at.get(transfer.source_id, -1e9) + TRANSFER_COOLDOWN_SECONDS > now
                or self._backoff.get("send:" + transfer.source_id, 0) > now
            ):
                continue
            if self.abandoned:
                return sent
            used_sources.add(transfer.source_id)
            if sent:
                time.sleep(CLICK_PAUSE_SECONDS)
            label = f"{source.name} → {target.name}"
            try:
                accepted = self._executor.send_resources(
                    source.id, source.marketplace.location, target.name,
                    target.x, target.y, transfer.resources,
                )
            except GoldGuardError as error:
                self.halted = str(error)
                self._log(f"{label}: STOP — {error}")
                return sent
            except ClickModeError as error:
                self._backoff["send:" + source.id] = now + FAILURE_BACKOFF_SECONDS
                self._log(f"{label}: nie wysłano — {error}")
                continue
            except Exception as error:
                logger.exception("Resource send failed")
                self._backoff["send:" + source.id] = now + FAILURE_BACKOFF_SECONDS
                self._log(f"{label}: błąd przeglądarki — {error}")
                self._executor.close()
                continue
            sent = True
            self._sent_at[source.id] = now
            amounts = " / ".join(f"{accepted.get(r, 0):,}".replace(",", " ") for r in vb.RESOURCES)
            self._log(f"{label}: wysłano D/G/Ż/Z {amounts} ({transfer.reason}).")
        return sent

    # -- crop bank ----------------------------------------------------------------
    def _mill(self, snapshot: BuildSnapshot, settings) -> bool:
        """The crop bank's granaries are nearly full: the NPC merchant turns
        the crop into the wood, clay and iron the network lacks (gold)."""
        bank_id = snapshot.crop_bank
        plan = next((p for p in snapshot.plans if p.village.id == bank_id), None)
        if plan is None or bank_id in settings.disabled_villages:
            return False
        village = plan.village
        now = time.monotonic()
        if village.marketplace is None or self._backoff.get("mill:" + bank_id, 0) > now:
            return False
        gains = vb.crop_bank_mill(
            plan, snapshot.plans, settings.crop_bank_fill_percent / 100, settings.crop_bank_min_amount,
            snapshot.server_time, snapshot.merchant_speed, snapshot.world_speed,
            int((snapshot.hero or {}).get("type") or 0),
            self._hero_amount_all or float((snapshot.hero or {}).get("points") or 0) * HERO_ALL_PER_POINT,
        )
        if not gains:
            return False
        price = snapshot.npc_price
        if snapshot.gold - price < settings.gold_reserve:
            if not self._bank_gold_noted:
                self._bank_gold_noted = True
                self._log(
                    f"Bank zboża w {village.name}: spichlerze pełne, ale wymiana za {price} złota "
                    f"zeszłaby poniżej rezerwy {settings.gold_reserve} złota."
                )
            return False
        self._bank_gold_noted = False
        if self.abandoned:
            return False
        logger.info("Village build action: %s crop bank NPC %s", village.name, gains)
        try:
            result = self._executor.npc_convert(bank_id, village.marketplace.location, gains, price)
        except GoldGuardError as error:
            self.halted = str(error)
            self._log(f"Bank zboża: STOP — {error}")
            return False
        except Exception as error:
            self._backoff["mill:" + bank_id] = now + FAILURE_BACKOFF_SECONDS
            self._log(f"Bank zboża w {village.name}: wymiana nie powiodła się — {error}")
            return False
        spent = int(result.get("spent") or 0)
        settings.crop_bank_gold_spent += spent
        self.budget_changed = True
        made = result.get("gains") or gains
        amounts = " / ".join(f"{int(made.get(r, 0)):,}".replace(",", " ") for r in (1, 2, 3))
        self._log(
            f"Bank zboża w {village.name}: zmielono {int(result.get('crop') or 0):,} zboża ".replace(",", " ")
            + f"na D/G/Ż {amounts} za {spent} złota."
        )
        return True

    # -- raid protection ----------------------------------------------------------
    def _protect(self, snapshot: BuildSnapshot, settings, imminent: dict[str, float]) -> bool:
        """An attack lands soon: building already took what it could this
        tick; send the rest of the lootable stock to safe villages, then
        train the overflow unit with what still cannot leave."""
        now = time.monotonic()
        acted = False
        plans = {plan.village.id: plan for plan in snapshot.plans}
        donors = {
            village_id for village_id in imminent
            if village_id in plans and village_id not in settings.disabled_villages
        }
        if not donors:
            return False
        shipped = {village_id: {r: 0.0 for r in vb.RESOURCES} for village_id in donors}
        evacuations = [
            e for e in vb.plan_evacuation(
                snapshot.plans, donors, set(snapshot.threats),
                snapshot.merchant_speed, snapshot.world_speed,
            )
            if self._backoff.get("raid:" + e.source_id, 0) <= now
        ]
        for evacuation in evacuations:
            source = plans[evacuation.source_id].village
            target = plans[evacuation.target_id].village
            minutes = max(0, int((imminent[source.id] - snapshot.server_time) // 60))
            label = f"{source.name} → {target.name}"
            if self.abandoned:
                return acted
            if acted:
                time.sleep(CLICK_PAUSE_SECONDS)
            logger.info("Village build action: raid evacuation %s", label)
            try:
                accepted = self._executor.send_resources(
                    source.id, source.marketplace.location, target.name,
                    target.x, target.y, evacuation.resources,
                )
            except GoldGuardError as error:
                self.halted = str(error)
                self._log(f"{label}: STOP — {error}")
                return acted
            except Exception as error:
                self._backoff["raid:" + source.id] = now + 60
                self._log(f"{label}: nie wysłano przed atakiem — {error}")
                continue
            acted = True
            self._sent_at[source.id] = now
            for r in vb.RESOURCES:
                shipped[source.id][r] += float(accepted.get(r, 0) or 0)
            amounts = " / ".join(f"{accepted.get(r, 0):,}".replace(",", " ") for r in vb.RESOURCES)
            self._log(
                f"{label}: wysłano D/G/Ż/Z {amounts} — atak na {source.name} za {minutes} min, "
                f"surowce u kupców i w {target.name} są bezpieczne."
            )
        for village_id in donors:
            village = plans[village_id].village
            minutes = max(0, int((imminent[village_id] - snapshot.server_time) // 60))
            exposed = vb.raid_exposed(village)
            left = {r: max(0.0, exposed[r] - shipped[village_id][r]) for r in vb.RESOURCES}
            unit = settings.overflow_units.get(village_id)
            if not unit and settings.overflow_auto and village.recruit_buildings:
                unit = vb.choose_overflow_unit(village, snapshot.troops, left)
            info = snapshot.troops.get(int(unit)) if unit else None
            count = 0
            if (
                info is not None
                and set(info["building_types"]) & village.recruit_buildings
                and self._backoff.get("raidtrain:" + village_id, 0) <= now
            ):
                count = vb.raid_recruitment(left, info["costs"])
            if count > 0:
                if self.abandoned:
                    return acted
                if acted:
                    time.sleep(CLICK_PAUSE_SECONDS)
                logger.info("Village build action: %s raid recruit %s x %s", village.name, count, unit)
                try:
                    result = self._executor.recruit(
                        village.id, info["building_types"], int(unit), count, 1, 10**9, 1.0,
                        settings.overflow_max_queue_hours * 3600,
                        village.unit_times.get(int(unit), 0),
                    )
                except GoldGuardError as error:
                    self.halted = str(error)
                    self._log(f"{village.name}: STOP — {error}")
                    return acted
                except Exception as error:
                    self._backoff["raidtrain:" + village_id] = now + 120
                    self._log(f"{village.name}: rekrutacja przed atakiem nie powiodła się — {error}")
                    continue
                acted = True
                if result.get("status") == "confirmed":
                    self._log(
                        f"{village.name}: atak za {minutes} min — z surowców, których nie dało się "
                        f"wysłać, zlecono {result.get('quantity')} × jednostka {unit}."
                    )
                continue
            key = (village_id, imminent[village_id])
            if key not in self._raid_noted and not any(e.source_id == village_id for e in evacuations):
                self._raid_noted.add(key)
                left_total = sum(left.values())
                why = (
                    "brak targowiska" if village.marketplace is None
                    else "brak wolnych kupców" if village.merchants_free <= 0
                    else "brak miejsca w bezpiecznych wioskach"
                )
                self._log(
                    f"{village.name}: atak za {minutes} min — "
                    + ("nic do zabezpieczenia." if left_total < vb.RAID_MIN_SHIPMENT
                       else f"zostaje ~{left_total:,.0f} surowców do złupienia ({why}).".replace(",", " "))
                )
        return acted

    # -- hero --------------------------------------------------------------------
    def _hero(self, snapshot: BuildSnapshot) -> bool:
        """Point the hero's production at the network's scarcest resource."""
        hero = snapshot.hero
        if not hero or hero.get("dead") or not hero.get("points"):
            return False
        now = time.monotonic()
        current = int(hero.get("type") or 0)
        # Per-resource amount of "all": read from the hero window once it was
        # opened; until then estimated from the points (1080 at 68 points).
        amount_all = self._hero_amount_all or float(hero["points"]) * HERO_ALL_PER_POINT
        # The hero's home village waits with an empty queue: produce what
        # it lacks right now (an idle queue there may be the network's end date).
        home = next((p for p in snapshot.plans if p.village.id == str(hero.get("village_id"))), None)
        stall = vb.hero_stall_choice(home, current, amount_all) if home is not None else None
        stalled = (
            home is not None and home.queue_free and home.next_step is not None
            and home.afford_hours is not None and home.afford_hours > 0
        )
        if stall is not None and now >= self._hero_stall_next:
            best, before, after = stall
            if self._switch_hero(current, best, (
                f"kolejka w {home.village.name} stoi — {home.next_step.name} "
                f"{home.next_step.from_level}→{home.next_step.from_level + 1} ruszy za "
                f"{after * 60:.0f} min zamiast {before * 60:.0f} min"
            )):
                self._hero_for_stall = True
                self._hero_stall_next = now + HERO_STALL_SECONDS
                return True
            self._hero_stall_next = now + FAILURE_BACKOFF_SECONDS
            return False
        if self._hero_for_stall:
            if stalled:
                return False  # still waiting: keep producing for it
            # The stall is over: back to the long-term best at once.
            self._hero_next_check = now
        if now < self._hero_next_check:
            return False
        choice = vb.choose_hero_resource(snapshot.plans, current, amount_all)
        if choice is None:
            self._hero_for_stall = False
            self._hero_next_check = now + HERO_CHECK_SECONDS
            return False
        best, hours = choice
        waits = vb.hero_stall_waits(home, current, amount_all) if home is not None else None
        if waits and waits[best] > waits[current] + vb.HERO_STALL_MIN_SAVED_HOURS:
            # The home queue waits and the current setting ends that sooner
            # (e.g. set for the stall before a restart): keep it until it builds.
            self._hero_for_stall = True
            return False
        if self._switch_hero(current, best, (
            f"najrzadszy surowiec starczy na cele po {hours[best]:.0f} h zamiast "
            f"{hours[current]:.0f} h produkcji sieci"
        )):
            self._hero_for_stall = False
            self._hero_next_check = now + HERO_SWITCH_SECONDS
            return True
        return False

    def _switch_hero(self, current: int, best: int, why: str) -> bool:
        now = time.monotonic()
        if self.abandoned:
            return False
        logger.info("Village build action: hero production %s -> %s", current, best)
        try:
            shown = self._executor.set_hero_production(best)
        except GoldGuardError as error:
            self.halted = str(error)
            self._log(f"Bohater: STOP — {error}")
            return False
        except Exception as error:
            self._hero_next_check = now + FAILURE_BACKOFF_SECONDS
            self._log(f"Bohater: nie przestawiono produkcji — {error}")
            return False
        if shown.get("all"):
            self._hero_amount_all = float(shown["all"])
        self._log(f"Bohater: produkcja {HERO_NAMES[current]} → {HERO_NAMES[best]} ({why}).")
        return True

    # -- army --------------------------------------------------------------------
    def _queue_end(self, village, building_type: int) -> float:
        known = self._training_until.get((village.id, building_type), 0.0)
        read = max(
            village.unit_queue_end.get(building_type, 0.0),
            village.unit_queue_end.get(building_type + 10, 0.0),
        )
        return max(known, read)

    def _military_plan(self, villages, settings, server_time):
        """Army villages active now: each next one starts once the previous
        trains without pause. Returns (target levels per village, villages
        with resources to spare that build barracks/stable first, active
        config entries)."""
        by_id = {v.id: v for v in villages}
        active = []
        for entry in settings.military_villages or []:
            village = by_id.get(str(entry.get("village_id")))
            if village is None:
                continue
            active.append(entry)
            units = {int(k): int(v) for k, v in (entry.get("units") or {}).items()}
            present = [b for b in units if b in village.recruit_buildings]
            saturated = bool(present) and all(
                self._queue_end(village, building) - server_time
                >= settings.military_saturated_hours * 3600
                for building in present
            )
            if not saturated:
                break
        previous = self.snapshot.network
        targets = {}
        first = set()
        for entry in active:
            village_id = str(entry["village_id"])
            level = int(entry.get("target_level") or 20)
            targets[village_id] = {int(k): level for k in (entry.get("units") or {})}
            if previous is not None and village_id in previous.surplus:
                first.add(village_id)
        return targets, first, active

    def _train(self, snapshot, settings) -> bool:
        """Keep the chosen units training in the active army villages, paid
        from what building does not need now (next step and queue unblock
        stay reserved)."""
        acted = False
        plans = {plan.village.id: plan for plan in snapshot.plans}
        for entry in self._military_active:
            plan = plans.get(str(entry.get("village_id")))
            if plan is None or plan.village.id in settings.disabled_villages:
                continue
            village = plan.village
            if self._backoff.get("train:" + village.id, 0) > time.monotonic():
                continue
            units = {int(k): int(v) for k, v in (entry.get("units") or {}).items()}
            present = [b for b in units if b in village.recruit_buildings]
            if not present:
                continue
            # Next step and the next unreserved master entry stay for building.
            reserve = vb.build_reserve(plan)
            spare = {
                r: village.build_resources()[r] - reserve.get(r, 0)
                for r in vb.RESOURCES
            }
            for building in present:
                unit = units[building]
                info = snapshot.troops.get(unit)
                if info is None:
                    continue
                left = self._queue_end(village, building) - snapshot.server_time
                if left >= settings.military_queue_hours * 3600 - 600:
                    continue
                share = 1.0 / len(present)
                costs = info["costs"]
                count = min(
                    int(max(0.0, spare[r]) * share // costs[r])
                    for r in vb.RESOURCES if costs.get(r, 0) > 0
                )
                if count < 5:
                    continue
                if self.abandoned:
                    return acted
                if acted:
                    time.sleep(CLICK_PAUSE_SECONDS)
                logger.info("Village build action: %s train %s x %s", village.name, count, unit)
                try:
                    result = self._executor.recruit(
                        village.id, info["building_types"], unit, count, 1, 10**9, 1.0,
                        settings.military_queue_hours * 3600, village.unit_times.get(unit, 0),
                    )
                except GoldGuardError as error:
                    self.halted = str(error)
                    self._log(f"{village.name}: STOP — {error}")
                    return acted
                except Exception as error:
                    self._backoff["train:" + village.id] = time.monotonic() + FAILURE_BACKOFF_SECONDS
                    self._log(f"{village.name}: szkolenie nie powiodło się — {error}")
                    continue
                if result.get("status") == "confirmed":
                    acted = True
                    self._training_until[(village.id, building)] = (
                        snapshot.server_time + float(result.get("queue_seconds") or 0)
                    )
                    for r in vb.RESOURCES:
                        spare[r] -= costs.get(r, 0) * int(result.get("quantity") or 0)
                    hours = float(result.get("queue_seconds") or 0) / 3600
                    self._log(
                        f"{village.name}: szkolenie {result.get('quantity')} × jednostka {unit} "
                        f"(kolejka {hours:.1f} h)."
                    )
        return acted

    def _burn_overflow(self, snapshot: BuildSnapshot, settings) -> bool:
        """Train the chosen unit where resources would otherwise be lost."""
        acted = False
        for plan in snapshot.plans:
            village = plan.village
            shipped = {r: 0.0 for r in vb.RESOURCES}
            for transfer in snapshot.transfers:
                if transfer.source_id == village.id:
                    for r, amount in transfer.resources.items():
                        shipped[r] += amount
            unit = settings.overflow_units.get(village.id)
            if not unit and settings.overflow_auto and village.recruit_buildings:
                excess = vb.overflow_excess(plan, settings.overflow_hours, shipped)
                if any(value > 0 for value in excess.values()):
                    unit = vb.choose_overflow_unit(village, snapshot.troops, excess)
            info = snapshot.troops.get(int(unit)) if unit else None
            if (
                info is None
                or village.id in settings.disabled_villages
                or self._backoff.get("burn:" + village.id, 0) > time.monotonic()
                or not set(info["building_types"]) & village.recruit_buildings
            ):
                continue
            count = vb.overflow_recruitment(
                plan, info["costs"], settings.overflow_hours, shipped,
                settings.master_builder_slots,
            )
            if count <= 0:
                continue
            if self.abandoned:
                return acted
            if acted:
                time.sleep(CLICK_PAUSE_SECONDS)
            logger.info("Village build action: %s overflow recruit %s x %s", village.name, count, unit)
            try:
                result = self._executor.recruit(
                    village.id, info["building_types"], int(unit), count, 1, 10**9, 1.0,
                    settings.overflow_max_queue_hours * 3600,
                    village.unit_times.get(int(unit), 0),
                )
            except GoldGuardError as error:
                self.halted = str(error)
                self._log(f"{village.name}: STOP — {error}")
                return acted
            except Exception as error:
                self._backoff["burn:" + village.id] = time.monotonic() + FAILURE_BACKOFF_SECONDS
                self._log(f"{village.name}: rekrutacja nadmiaru nie powiodła się — {error}")
                continue
            acted = True
            if result.get("status") == "confirmed":
                self._log(
                    f"{village.name}: magazyn przepełniłby się — zlecono "
                    f"{result.get('quantity')} × jednostka {unit} (w kolejce {result.get('count')})."
                )
        return acted

    def close(self) -> None:
        self._executor.close()
