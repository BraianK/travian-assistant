"""Tests for the resource-field build planner and transfer advisor."""

import pytest

from src.core import village_builder as vb


def _field(location, type_, level, cost=100, gain_per_level=10, max_level=10, supply_step=1, queued=0):
    levels = range(level, level + 7)
    return vb.Building(
        location=location,
        type=type_,
        level=level,
        max_level=max_level,
        costs={lvl: {r: cost * (lvl + 1) for r in vb.RESOURCES} for lvl in levels},
        times={lvl: 600 for lvl in levels},
        effects={lvl: gain_per_level * lvl for lvl in range(level, level + 8)},
        supply={lvl: supply_step * lvl for lvl in range(level, level + 8)},
        queued=queued,
    )


def _storage(location, type_, level, capacity_step=1000):
    levels = range(level, level + 7)
    return vb.Building(
        location=location,
        type=type_,
        level=level,
        max_level=20,
        costs={lvl: {r: 10 for r in vb.RESOURCES} for lvl in levels},
        times={lvl: 600 for lvl in levels},
        effects={lvl: capacity_step * lvl for lvl in range(level, level + 8)},
        supply={lvl: lvl for lvl in range(level, level + 8)},
    )


def _village(fields, storages=(), capital=False, resources=10_000, capacity=100_000,
             production=None, vid="1", x=0, y=0, merchants=0, carry=0, free=1):
    return vb.Village(
        id=vid, name=f"V{vid}", is_capital=capital, x=x, y=y, tribe=3,
        resources={r: resources for r in vb.RESOURCES},
        capacity={r: capacity for r in vb.RESOURCES},
        production=production or {1: 100, 2: 100, 3: 100, 4: 100},
        fields=list(fields), storages=list(storages),
        free_slots={1: free, 2: free, 4: 0},
        merchants_free=merchants, merchant_carry=carry,
    )


def test_picks_cheapest_payback_first():
    cheap = _field(1, 1, 3, cost=10)
    dear = _field(2, 2, 3, cost=50)
    plan = vb.plan_village(_village([cheap, dear]), target=10, server_time=0)
    assert plan.next_step.location == 1
    assert plan.ready_now


def test_respects_target_level_per_village_type():
    field = _field(1, 1, 10, max_level=20)
    capital = _village([field], capital=True)
    other = _village([_field(1, 1, 10, max_level=20)])
    assert vb.plan_village(capital, target=14, server_time=0).next_step is not None
    plan = vb.plan_village(other, target=10, server_time=0)
    assert plan.next_step is None and plan.finished
    assert vb.target_level(capital, 14, 10) == 14
    assert vb.target_level(other, 14, 10) == 10


def test_game_max_level_caps_target():
    plan = vb.plan_village(_village([_field(1, 1, 10, max_level=10)]), target=14, server_time=0)
    assert plan.finished


def test_storage_upgrade_comes_first_when_cost_does_not_fit():
    field = _field(1, 1, 5, cost=1000)  # costs 6000 of each resource
    warehouse = _storage(30, 10, 4, capacity_step=1000)
    granary = _storage(31, 11, 9, capacity_step=1000)
    village = _village([field], [warehouse, granary], capacity=4000)
    village.capacity[4] = 9000
    plan = vb.plan_village(village, target=10, server_time=0)
    assert plan.next_step.type == 10
    assert "magazyn" in plan.next_step.reason
    # Warehouse 4000 -> 6000 and granary 9000 is enough: then the field.
    types = [step.type for step in plan.steps[:3]]
    assert types == [10, 10, 1]


def test_granary_upgrade_when_crop_cost_does_not_fit():
    field = _field(1, 1, 5, cost=1000)
    warehouse = _storage(30, 10, 9)
    granary = _storage(31, 11, 4)
    village = _village([field], [warehouse, granary], capacity=4000)
    village.capacity[1] = village.capacity[2] = village.capacity[3] = 9000
    plan = vb.plan_village(village, target=10, server_time=0)
    assert [step.type for step in plan.steps[:3]] == [11, 11, 1]
    assert "spichlerz" in plan.next_step.reason


def test_blocked_without_storage_building():
    village = _village([_field(1, 1, 5, cost=1000)], capacity=4000)
    plan = vb.plan_village(village, target=10, server_time=0)
    assert plan.next_step is None
    assert "magazynu" in plan.blocked


def test_cropland_first_when_crop_would_go_negative():
    wood = _field(1, 1, 3, cost=10, supply_step=5)
    crop = _field(2, 4, 3, cost=50, supply_step=0)
    village = _village([wood, crop], production={1: 100, 2: 100, 3: 100, 4: 2})
    plan = vb.plan_village(village, target=10, server_time=0, balanced=False)
    assert plan.next_step.type == 4
    assert "zboża" in plan.next_step.reason
    # Valued by scarcity, crop (2/h) is the bottleneck: cropland anyway.
    assert vb.plan_village(village, target=10, server_time=0).next_step.type == 4


def test_field_in_queue_is_not_the_first_click():
    busy = _field(1, 1, 3, cost=10, queued=1)
    other = _field(2, 2, 3, cost=50)
    plan = vb.plan_village(_village([busy, other]), target=10, server_time=0)
    assert plan.next_step.location == 2


def test_afford_and_queue_hours():
    village = _village([_field(1, 1, 3, cost=100)], resources=0, free=0)
    village.busy_until = 7200
    plan = vb.plan_village(village, target=10, server_time=0)
    assert plan.afford_hours == 4.0  # 400 of each at 100/h
    assert plan.queue_hours == 2.0
    assert plan.ready_hours == 4.0
    assert not plan.ready_now


def test_transfer_recommended_when_it_saves_time():
    receiver = _village([_field(1, 1, 3, cost=1000)], resources=0, vid="1", x=0, y=0)
    donor = _village([_field(1, 1, 9, max_level=10)], resources=50_000, vid="2", x=0, y=24,
                     merchants=10, carry=2000)
    plans = [vb.plan_village(v, 10, 0) for v in (receiver, donor)]
    transfers = vb.plan_transfers(plans, {3: 24}, 1)
    assert len(transfers) == 1
    transfer = transfers[0]
    assert transfer.source_id == "2" and transfer.target_id == "1"
    assert transfer.travel_hours == 1.0
    assert transfer.wait_before_hours == 40.0
    assert transfer.wait_after_hours == 1.0
    # The receiver can use more than its next step: all 10 merchants go.
    assert transfer.total == 20_000 and transfer.merchants == 10


def test_transfer_limited_by_free_merchants():
    receiver = _village([_field(1, 1, 3, cost=1000)], resources=0, vid="1")
    donor = _village([_field(1, 1, 9, max_level=10)], resources=50_000, vid="2", y=24,
                     merchants=10, carry=1000)
    plans = [vb.plan_village(v, 10, 0) for v in (receiver, donor)]
    transfer = vb.plan_transfers(plans, {3: 24}, 1)[0]
    assert transfer.total == 10_000 and transfer.merchants == 10
    assert transfer.wait_after_hours == 15.0  # the rest is produced at home


def test_no_transfer_to_queue_bound_village_that_can_pay_itself():
    receiver = _village([_slow_field(1, 1, 3)], resources=350, vid="1",
                        production={1: 1e5, 2: 1e5, 3: 1e5, 4: 1e5})
    donor = _village([], resources=50_000, vid="2", x=0, y=240, merchants=10, carry=1000)
    plans = [vb.plan_village(v, 10, 0) for v in (receiver, donor)]
    assert plans[0].queue_bound and plans[0].ready_now
    assert vb.plan_transfers(plans, {3: 24}, 1) == []


def test_donor_keeps_its_own_next_step():
    receiver = _village([_field(1, 1, 3, cost=1000)], resources=0, vid="1")
    donor = _village([_field(1, 1, 3, cost=1000)], resources=4000, vid="2", y=1,
                     merchants=10, carry=1000)
    plans = [vb.plan_village(v, 10, 0) for v in (receiver, donor)]
    assert vb.plan_transfers(plans, {3: 24}, 1) == []


def test_village_from_raw_reads_game_tables():
    raw = {
        "id": "5", "name": "A", "is_capital": True, "x": 1, "y": 2, "tribe": 3,
        "resources": {"1": 10, "2": 20, "3": 30, "4": 40},
        "capacity": {"1": "100", "2": "100", "3": "100", "4": "200"},
        "production": {"1": "9", "2": "8", "3": "7", "4": "6"},
        "free_slots": {"1": 0, "2": 1, "4": 0},
        "queues": {"1": [], "2": [{"finished": "500", "locationId": "3"}]},
        "merchants_free": 2, "merchant_carry": 750,
        "buildings": [
            {"locationId": 3, "buildingType": 1, "lvl": 10, "lvlMax": 99,
             "nextUpgradeCosts": {"10": {"1": 6581, "2": 16448, "3": 8224, "4": 9871}},
             "nextUpgradeTimes": {"10": 13110},
             "upgradeSupplyUsageSums": {"10": 16, "11": 18},
             "effect": [600, 840], "queued": 1},
            {"locationId": 30, "buildingType": 10, "lvl": 14, "lvlMax": 20,
             "nextUpgradeCosts": {}, "effect": [39000, 48000]},
        ],
    }
    village = vb.village_from_raw(raw)
    assert village.is_capital and village.busy_until == 500
    field = village.fields[0]
    assert field.effects == {10: 600.0, 11: 840.0}
    assert field.costs[10][2] == 16448
    assert field.queued == 1 and field.planned_level == 11
    assert village.storages[0].effects[15] == 48000
    assert village.free_slots == {1: 0, 2: 1, 4: 0}


def _main_building(location=26, level=7, effects=(80, 77, 75, 72, 69, 67, 64, 62), time=900):
    return vb.Building(
        location=location, type=vb.MAIN_BUILDING, level=level, max_level=20,
        costs={lvl: {r: 100 for r in vb.RESOURCES} for lvl in range(level, level + 7)},
        times={lvl: time for lvl in range(level, level + 7)},
        effects={level + i: float(v) for i, v in enumerate(effects)},
        supply={lvl: lvl for lvl in range(level, level + 8)},
    )


def _slow_field(location, type_, level):
    field = _field(location, type_, level, cost=10)
    field.times = {lvl: 36_000 for lvl in field.times}  # 10 h per level
    return field


def test_main_building_first_when_queue_bound():
    village = _village([_slow_field(1, 1, 3), _slow_field(2, 2, 3)], production={1: 1e5, 2: 1e5, 3: 1e5, 4: 1e5})
    village.main_building = _main_building()
    plan = vb.plan_village(village, target=10, server_time=0)
    assert plan.queue_bound
    assert plan.next_step.type == vb.MAIN_BUILDING
    assert "kolejka" in plan.next_step.reason
    # Disabled by the setting.
    off = vb.plan_village(village, target=10, server_time=0, main_building=False)
    assert off.next_step.type != vb.MAIN_BUILDING


def test_no_main_building_when_resource_bound():
    village = _village([_field(1, 1, 3, cost=1000)], production={1: 1, 2: 1, 3: 1, 4: 1})
    village.main_building = _main_building()
    plan = vb.plan_village(village, target=10, server_time=0)
    assert not plan.queue_bound
    assert plan.next_step.type == 1


def test_main_building_stops_when_gain_too_small():
    village = _village([_slow_field(1, 1, 9)], production={1: 1e5, 2: 1e5, 3: 1e5, 4: 1e5})
    village.main_building = _main_building(time=36_000)  # 10 h, saves ~0.4 h
    plan = vb.plan_village(village, target=10, server_time=0)
    assert plan.next_step.type == 1


def test_npc_afford_uses_total():
    village = _village([_field(1, 1, 3, cost=100)])
    village.resources = {1: 0, 2: 1000, 3: 1000, 4: 1000}
    plan = vb.plan_village(village, target=10, server_time=0)
    assert plan.afford_hours == 4.0
    assert plan.afford_hours_npc == 0.0
    assert plan.npc_ready_now and not plan.ready_now


def test_finish_tiers_and_usefulness():
    tiers = vb.FinishTiers(allow_free=True, allow_reduced=True, allow_full=False)
    assert tiers.price(299) == 0 and tiers.price(300) == 1 and tiers.price(7200) == 2
    village = _village([_field(1, 1, 3, cost=100)], free=0)
    village.queue = [vb.QueueEntry(2, 5, 1, finished=5400)]
    plan = vb.plan_village(village, target=10, server_time=0)
    offers = vb.finish_offers(plan, tiers, server_time=0)
    assert offers[0].price == 1 and offers[0].allowed and offers[0].useful
    # Paid finish is not useful when the next step still waits for resources.
    poor = _village([_field(1, 1, 3, cost=100)], resources=0, free=0)
    poor.queue = [vb.QueueEntry(2, 5, 1, finished=5400)]
    poor_plan = vb.plan_village(poor, target=10, server_time=0)
    assert not vb.finish_offers(poor_plan, tiers, 0)[0].useful
    # A free finish is always useful; long builds need allow_full.
    assert vb.finish_offers(poor_plan, tiers, 5300)[0].useful
    assert not vb.finish_offers(plan, tiers, -9000)[0].allowed


def test_queue_bound_donor_sends_surplus_to_resource_bound_village():
    donor = _village([_slow_field(1, 1, 3)], resources=20_000, vid="1",
                     production={1: 1e4, 2: 1e4, 3: 1e4, 4: 1e4}, merchants=20, carry=1000)
    receiver = _village([_field(1, 1, 3, cost=1000)], resources=5000, vid="2", y=1,
                        production={1: 10, 2: 10, 3: 10, 4: 10})
    plans = [vb.plan_village(v, 10, 0) for v in (donor, receiver)]
    assert plans[0].queue_bound and not plans[1].queue_bound
    transfers = vb.plan_transfers(plans, {3: 24}, 1)
    assert len(transfers) == 1
    transfer = transfers[0]
    assert transfer.source_id == "1" and transfer.target_id == "2"
    # More than the next step: the receiver can use it for its later steps.
    assert transfer.total > sum(plans[1].next_step.costs.values()) - 4 * 5000
    assert "nadwyżka" in transfer.reason


def test_finish_windows_follow_player_settings():
    tiers = vb.FinishTiers(
        allow_free=True, allow_reduced=True, allow_full=True,
        reduced_min_seconds=3600, full_min_seconds=10800,
    )
    # 20 min left: too short to pay, waits 15 min for the free finish.
    assert not tiers.allowed(1200)
    assert tiers.wait_seconds(1200) == 1200 - 300 + 1
    # 2 h 20 min left: waits 20 min (+1 s) for the 1 gold price.
    assert not tiers.allowed(8400)
    assert tiers.wait_seconds(8400) == 8400 - 7200 + 1
    assert tiers.price(8400 - tiers.wait_seconds(8400)) == 1
    # 1 h 30 min: pays 1 gold now; 4 h: pays 2 gold now.
    assert tiers.allowed(5400) and tiers.price(5400) == 1
    assert tiers.allowed(14400) and tiers.price(14400) == 2
    # Under 5 min: free.
    assert tiers.allowed(200) and tiers.price(200) == 0


def test_finish_offer_waits_for_cheaper_window():
    tiers = vb.FinishTiers(allow_reduced=True, allow_full=True)
    village = _village([_field(1, 1, 3, cost=100)], free=0)
    village.queue = [vb.QueueEntry(2, 5, 1, finished=8400)]
    plan = vb.plan_village(village, target=10, server_time=0)
    offer = vb.finish_offers(plan, tiers, server_time=0)[0]
    assert not offer.allowed and offer.useful
    assert offer.next_price == 1 and offer.wait_seconds == 1201
    assert "czeka 21 min" in offer.reason
    # Short build: waits for the free finish even when resources wait.
    village.queue = [vb.QueueEntry(2, 5, 1, finished=1200)]
    short = vb.finish_offers(vb.plan_village(village, 10, 0), tiers, 0)[0]
    assert short.next_price == 0 and not short.allowed


def test_one_finish_offer_per_queue_type():
    village = _village([_field(1, 1, 3, cost=100)], free=0)
    village.queue = [
        vb.QueueEntry(2, 5, 1, finished=200),
        vb.QueueEntry(2, 6, 1, finished=200),
        vb.QueueEntry(4, 7, 1, finished=0),
    ]
    plan = vb.plan_village(village, target=10, server_time=0)
    offers = vb.finish_offers(plan, vb.FinishTiers(), server_time=0)
    assert len(offers) == 1 and offers[0].price == 0 and offers[0].allowed



def _equal_cost_field(location, type_, level, crop_cost=0):
    field = _field(location, type_, level, cost=100)
    for lvl, row in field.costs.items():
        row[4] = crop_cost * (lvl + 1)
    return field


def test_surplus_crop_is_worth_less():
    one = {r: 1.0 for r in vb.RESOURCES}
    wood = _field(1, 1, 3, cost=100)  # +10/h
    crop = _field(2, 4, 3, cost=100)
    crop.effects = {lvl: 11 * lvl for lvl in crop.effects}  # +11/h
    village = _village([wood, crop])
    # 1:1 the cropland pays back faster.
    assert (vb._field_step(village, crop, 3, one).payback_hours
            < vb._field_step(village, wood, 3, one).payback_hours)
    # Costs need 25 % crop, but crop is half of the production: worth less.
    weights = vb.resource_weights({r: 1 for r in vb.RESOURCES}, {1: 100, 2: 100, 3: 100, 4: 300})
    assert weights == {1: 1.5, 2: 1.5, 3: 1.5, 4: 0.5}
    assert (vb._field_step(village, wood, 3, one, weights=weights).payback_hours
            < vb._field_step(village, crop, 3, one, weights=weights).payback_hours)


def test_scarce_crop_is_worth_more():
    wood = _equal_cost_field(1, 1, 3, crop_cost=100)
    crop = _equal_cost_field(2, 4, 3)
    crop.effects = {lvl: 9 * lvl for lvl in crop.effects}
    village = _village([wood, crop], production={1: 1000, 2: 1000, 3: 1000, 4: 50})
    assert vb.plan_village(village, 10, 0, balanced=False).next_step.type == 1
    assert vb.plan_village(village, 10, 0).next_step.type == 4


def test_resource_weights_average_to_one_by_production():
    production = {1: 100, 2: 100, 3: 100, 4: 300}
    weights = vb.resource_weights({1: 30, 2: 30, 3: 30, 4: 10}, production)
    total = sum(production.values())
    assert abs(sum(weights[r] * production[r] / total for r in vb.RESOURCES) - 1) < 0.2
    assert weights[4] < 1 < weights[1]


def test_incoming_loot_counts_from_arrival():
    resources = {1: 0, 2: 0, 3: 0, 4: 0}
    production = {1: 100, 2: 100, 3: 100, 4: 100}
    costs = {1: 1000, 2: 1000, 3: 1000, 4: 1000}
    assert vb.time_to_afford(costs, resources, production) == 10.0
    loot = [(0.5, {1: 950, 2: 950, 3: 950, 4: 950})]
    assert vb.time_to_afford(costs, resources, production, loot) == 0.5
    # Loot that does not fit the storage is lost.
    capacity = {r: 800 for r in vb.RESOURCES}
    # At 0.5 h: 50 + 950 = 1000, but only 800 fit; the last 200 take 2 h.
    assert vb.time_to_afford(costs, resources, production, loot, capacity) == 2.5
    assert vb.time_to_afford_total(costs, resources, production, loot) == 0.5


def test_village_incoming_changes_plan_eta():
    village = _village([_field(1, 1, 3, cost=100)], resources=0)
    village.incoming = [(1800, {1: 400, 2: 400, 3: 400, 4: 400})]
    plan = vb.plan_village(village, target=10, server_time=0)
    assert plan.afford_hours == 0.5
    raw = {"id": "1", "incoming": [{"arrival": 100, "resources": {"1": 5, "2": 0}}],
           "buildings": []}
    assert vb.village_from_raw(raw).incoming == [(100.0, {1: 5.0})]


def _bonus(location, type_, level=4, cost=100):
    return vb.Building(
        location=location, type=type_, level=level, max_level=5,
        costs={level: {r: cost for r in vb.RESOURCES}},
        times={level: 3600},
        effects={level: 20.0, level + 1: 25.0},
        supply={level: 4, level + 1: 5},
    )


def test_bonus_building_competes_by_payback():
    village = _village([_field(1, 1, 3, cost=1000)], production={1: 1200, 2: 100, 3: 100, 4: 100})
    village.bonus = [_bonus(38, 5)]  # sawmill: +5 % of 1200 wood / 1.2 = +50/h for 400
    plan = vb.plan_village(village, 10, 0, balanced=False)
    step = plan.next_step
    assert step.type == 5 and abs(step.raw_gain - 50) < 1e-6
    assert "drewno" in step.reason
    assert vb.plan_village(village, 10, 0, balanced=False, bonus_buildings=False).next_step.type == 1


def test_storage_kept_for_hours_of_production():
    field = _field(1, 1, 3, cost=10)
    warehouse = _storage(30, 10, 4, capacity_step=1000)  # 4000 now
    granary = _storage(31, 11, 9, capacity_step=1000)
    village = _village([field], [warehouse, granary], capacity=4000,
                       production={1: 1000, 2: 100, 3: 100, 4: 100})
    plan = vb.plan_village(village, 10, 0, storage_hours=6)
    # 6 h of wood = 6000: two warehouse levels first.
    assert [s.type for s in plan.steps[:3]] == [10, 10, 1]
    assert "6 h" in plan.next_step.reason
    assert vb.plan_village(village, 10, 0).next_step.type == 1


def test_overflow_goes_to_any_village_with_room():
    # Donor not queue-bound but its warehouse is about to overflow.
    donor = _village([_field(1, 1, 3, cost=5000)], resources=9_900, capacity=10_000, vid="1",
                     production={1: 1000, 2: 1000, 3: 1000, 4: 1000}, merchants=20, carry=1000)
    # Receiver is queue-bound (would not get "surplus" shipments).
    receiver = _village([_slow_field(1, 1, 3)], resources=0, vid="2", y=1,
                        production={1: 1000, 2: 1000, 3: 1000, 4: 1000})
    plans = [vb.plan_village(v, 10, 0) for v in (donor, receiver)]
    assert plans[1].queue_bound
    transfers = vb.plan_transfers(plans, {3: 24}, 1)
    assert transfers and transfers[0].source_id == "1"
    assert "przepełni" in transfers[0].reason


def test_no_shipments_to_village_that_cannot_spend_and_would_overflow():
    # Receiver: queue busy for 2 h, master builder full and reserved, its own
    # production would fill the storage before it can spend anything.
    receiver = _village([_field(1, 1, 3, cost=1000)], resources=6_000, capacity=10_000, vid="1",
                        production={1: 1500, 2: 1500, 3: 1500, 4: 1500}, free=0)
    receiver.free_slots = {1: 0, 2: 0, 4: 0}
    receiver.busy_until = 7200
    receiver.queue = [vb.QueueEntry(2, 1, 1, finished=7200)] + [
        vb.QueueEntry(4, 1, 1, 0, id=str(i), paid=True) for i in range(4)
    ]
    donor = _village([], resources=50_000, vid="2", y=1, merchants=10, carry=1000)
    plans = [vb.plan_village(v, 10, 0) for v in (receiver, donor)]
    # 2 h running + 4 paid entries of 10 min each run before anything new.
    assert abs(vb.idle_hours(plans[0]) - (2 + 4 / 6)) < 1e-9
    # 10 000 - 6 000 - 1 500 x (2.7 + 1) h < 0: no room at all.
    assert vb.plan_transfers(plans, {3: 24}, 1) == []


def test_incoming_is_not_shipped_twice():
    receiver = _village([_field(1, 1, 3, cost=1000)], resources=0, vid="1")
    receiver.incoming = [(10**9, {r: 100_000 for r in vb.RESOURCES})]
    donor = _village([], resources=50_000, vid="2", y=1, merchants=10, carry=2000)
    plans = [vb.plan_village(v, 10, 0) for v in (receiver, donor)]
    assert vb.plan_transfers(plans, {3: 24}, 1) == []


def test_merchants_estimated_from_marketplace_level():
    raw = {"id": "1", "merchants_free": None, "merchant_carry": 0, "buildings": [
        {"locationId": 20, "buildingType": 17, "lvl": 14, "lvlMax": 20}]}
    village = vb.village_from_raw(raw)
    assert village.marketplace.location == 20 and village.merchants_free == 14
    no_market = vb.village_from_raw({"id": "2", "merchants_free": None, "buildings": []})
    assert no_market.merchants_free == 0 and no_market.marketplace is None


class _FakeExecutor:
    def __init__(self, error):
        self.error = error
        self.calls = 0

    def upgrade(self, *args):
        self.calls += 1
        raise self.error

    def close(self):
        pass


def test_gold_guard_halts_automation():
    from src.config.settings import BuildSettings
    from src.core.build_automation import BuildAutomation, BuildSnapshot
    from src.core.build_clicker import GoldGuardError

    settings = BuildSettings(enabled=True)
    executor = _FakeExecutor(GoldGuardError("Saldo złota spadło o 20"))
    logs = []
    automation = BuildAutomation(None, lambda: settings, logs.append, executor=executor)
    villages = [_village([_field(1, 1, 3, cost=10)], vid=str(i)) for i in (1, 2)]
    snapshot = BuildSnapshot(plans=[vb.plan_village(v, 10, 0) for v in villages], gold=100)
    automation._act(snapshot, settings)
    assert automation.halted and executor.calls == 1  # second village untouched
    assert any("STOP" in line for line in logs)


def test_recruitment_consumption_and_reserve():
    village = _village([_field(1, 1, 3, cost=100)], resources=500,
                       production={1: 100, 2: 100, 3: 100, 4: 100})
    # 400 of each needed, 500 in stock: ready now.
    assert vb.plan_village(village, 10, 0).ready_now
    # Recruitment eats 60/h of everything and keeps 2 h (120) in stock.
    village.recruitment = {r: 60.0 for r in vb.RESOURCES}
    village.recruitment_reserve = {r: 120.0 for r in vb.RESOURCES}
    plan = vb.plan_village(village, 10, 0)
    assert not plan.ready_now
    # 380 usable, 20 missing at 40/h left for building: 0.5 h.
    assert plan.afford_hours == 0.5


def test_recruiting_village_does_not_ship_its_reserve():
    donor = _village([], resources=5_000, vid="1", merchants=10, carry=1000,
                     production={1: 100, 2: 100, 3: 100, 4: 100})
    receiver = _village([_field(1, 1, 3, cost=1000)], resources=0, vid="2", y=1)
    plans = [vb.plan_village(v, 10, 0) for v in (donor, receiver)]
    assert vb.plan_transfers(plans, {3: 24}, 1)  # finished donor gives its stock
    donor.recruitment = {r: 2_500.0 for r in vb.RESOURCES}
    donor.recruitment_reserve = {r: 5_000.0 for r in vb.RESOURCES}
    plans = [vb.plan_village(v, 10, 0) for v in (donor, receiver)]
    assert vb.plan_transfers(plans, {3: 24}, 1) == []


def test_finish_value_prefers_more_hours_per_gold():
    tiers = vb.FinishTiers()
    # 7 h left: 2 gold now saves ~6.9 h (3.46 h/gold) > 1 gold later (1.9 h).
    value, price, now = vb.finish_value(7 * 3600, tiers)
    assert price == 2 and now and value > 3
    # 3 h left: waiting for the 1 gold price is better (1.92 h/gold).
    value, price, now = vb.finish_value(3 * 3600, tiers)
    assert price == 1 and not now
    # 1.5 h left: 1 gold now.
    value, price, now = vb.finish_value(1.5 * 3600, tiers)
    assert price == 1 and now


def test_gold_budget_goes_to_queue_bound_village():
    def slow(location, type_, level, hours):
        f = _field(location, type_, level, cost=10)
        f.times = {lvl: int(hours * 3600) for lvl in f.times}
        return f
    rich = _village([slow(i, 1 + i % 4, 3, 6) for i in range(1, 5)], vid="1",
                    production={1: 1e5, 2: 1e5, 3: 1e5, 4: 1e5})
    poor = _village([slow(1, 1, 3, 6)], vid="2", resources=0,
                    production={1: 1, 2: 1, 3: 1, 4: 1})
    plans = [vb.plan_village(v, 10, 0) for v in (rich, poor)]
    allocation = vb.plan_gold_budget(plans, 20, vb.FinishTiers(allow_reduced=True, allow_full=True))
    assert allocation.gold == 20
    assert set(allocation.gold_by_village) == {"1"}
    assert allocation.threshold > 2


def test_budget_offer_pays_now_or_waits():
    from src.core.build_automation import budget_offers

    tiers = vb.FinishTiers()
    village = _village([_field(1, 1, 3, cost=100)], free=0)
    village.queue = [vb.QueueEntry(2, 5, 1, finished=7 * 3600)]
    plan = vb.plan_village(village, 10, 0)
    allocation = vb.GoldAllocation(threshold=1.5, gold_by_village={}, hours_by_village={}, finishes_by_village={})
    offer = budget_offers(plan, tiers, 0, allocation, budget_left=10)[0]
    assert offer.allowed and offer.price == 2
    village.queue = [vb.QueueEntry(2, 5, 1, finished=3 * 3600)]
    offer = budget_offers(vb.plan_village(village, 10, 0), tiers, 0, allocation, 10)[0]
    assert not offer.allowed and offer.next_price == 1 and offer.wait_seconds == 3601
    # Over budget: waits for the free finish instead.
    offer = budget_offers(vb.plan_village(village, 10, 0), tiers, 0, allocation, 0)[0]
    assert offer.next_price == 0


class _RecordingExecutor:
    def __init__(self):
        self.calls = []

    def queue_master(self, village_id, location, current_level, is_field):
        self.calls.append(("master", village_id, location, current_level, is_field))
        return "queued"

    def upgrade(self, *args):
        self.calls.append(("upgrade",) + args)
        return "started"

    def close(self):
        pass


def test_busy_queue_lines_up_master_builder():
    from src.config.settings import BuildSettings
    from src.core.build_automation import BuildAutomation, BuildSnapshot

    settings = BuildSettings(enabled=True, master_builder_slots=2)
    village = _village([_field(1, 1, 3, cost=10)], free=0)
    village.free_slots = {1: 0, 2: 0, 4: 4}
    executor = _RecordingExecutor()
    automation = BuildAutomation(None, lambda: settings, lambda _t: None, executor=executor)
    snapshot = BuildSnapshot(plans=[vb.plan_village(village, 10, 0)], gold=0)
    assert automation._act(snapshot, settings)
    assert executor.calls == [("master", "1", 1, 3, True)]
    # Limit reached: nothing more is lined up.
    village.queue = [vb.QueueEntry(4, 1, 1, 0), vb.QueueEntry(4, 1, 1, 0)]
    executor.calls.clear()
    automation._act(BuildSnapshot(plans=[vb.plan_village(village, 10, 0)], gold=0), settings)
    assert executor.calls == []
    # No free master builder slot: never buys one.
    village.queue = []
    village.free_slots = {1: 0, 2: 0, 4: 0}
    automation._act(BuildSnapshot(plans=[vb.plan_village(village, 10, 0)], gold=0), settings)
    assert executor.calls == []


def test_master_replacements_only_unreserved_and_unwanted():
    cheap = _field(1, 1, 3, cost=10)
    dear = _field(2, 2, 3, cost=500)
    village = _village([cheap, dear], free=0)
    dear.queued = 1
    village.queue = [vb.QueueEntry(4, 2, 2, 0, id="7", paid=False)]
    replace = vb.master_replacements(village, 10, 0, slots=1)
    assert [e.id for e in replace] == ["7"]  # the cheap field is better
    # Reserved entries are never replaced.
    village.queue = [vb.QueueEntry(4, 2, 2, 0, id="7", paid=True)]
    assert vb.master_replacements(village, 10, 0, slots=1) == []
    # Nearly as good (a few steps down the plan): kept, no churn.
    close = _field(2, 2, 3, cost=11)
    close.queued = 1
    village.fields = [cheap, close]
    village.queue = [vb.QueueEntry(4, 2, 2, 0, id="7", paid=False)]
    assert vb.master_replacements(village, 10, 0, slots=2) == []


def _market(location=20, level=2):
    return vb.Building(
        location=location, type=vb.MARKETPLACE, level=level, max_level=20,
        costs={lvl: {r: 50 for r in vb.RESOURCES} for lvl in range(level, level + 7)},
        times={lvl: 600 for lvl in range(level, level + 7)},
        effects={}, supply={},
    )


def test_network_balance_reports_missing_merchants():
    donor = _village([_slow_field(1, 1, 3)], vid="1", production={1: 1e4, 2: 1e4, 3: 1e4, 4: 1e4},
                     merchants=2, carry=100)
    donor.marketplace = _market(level=2)
    receiver = _village([_field(1, 1, 3, cost=10_000)], vid="2", y=72,
                        production={1: 10, 2: 10, 3: 10, 4: 10})
    plans = [vb.plan_village(v, 10, 0) for v in (donor, receiver)]
    balance = vb.network_balance(plans, {3: 24}, 1)
    assert "1" in balance.surplus and "2" in balance.deficit
    assert balance.flow > 0
    # 72 fields at 24/h: 6 h round trip; 2 merchants x 100 cannot carry it.
    assert balance.extra_merchants.get("1", 0) > 0
    step = vb.marketplace_step(donor, balance.extra_merchants["1"])
    assert step.type == vb.MARKETPLACE and step.from_level == 2


def test_no_extra_merchants_when_neighbours_are_close():
    donor = _village([_slow_field(1, 1, 3)], vid="1", production={1: 100, 2: 100, 3: 100, 4: 100},
                     merchants=10, carry=1000)
    donor.marketplace = _market(level=10)
    receiver = _village([_field(1, 1, 3, cost=1000)], vid="2", y=1, production={1: 1, 2: 1, 3: 1, 4: 1})
    plans = [vb.plan_village(v, 10, 0) for v in (donor, receiver)]
    assert vb.network_balance(plans, {3: 24}, 1).extra_merchants == {}


def test_overflow_recruitment_rules():
    phalanx = {1: 85, 2: 100, 3: 50, 4: 0}
    village = _village([_field(1, 1, 3, cost=100)], resources=9_500, capacity=10_000,
                       production={1: 1000, 2: 1000, 3: 1000, 4: 1000}, free=0)
    village.free_slots = {1: 0, 2: 0, 4: 0}
    plan = vb.plan_village(village, 10, 0)
    # 500 over capacity of each: iron needs ceil(500/50) = 10 units (the most).
    assert vb.overflow_recruitment(plan, phalanx, 1.0) == 10
    # Shipments already take it away: nothing to burn.
    assert vb.overflow_recruitment(plan, phalanx, 1.0, shipped={1: 600, 2: 600, 3: 600}) == 0
    # The master builder still has room: it will reserve resources instead.
    village.free_slots = {1: 0, 2: 0, 4: 2}
    assert vb.overflow_recruitment(vb.plan_village(village, 10, 0), phalanx, 1.0) == 0
    # The next build step can start: building spends it.
    ready = _village([_field(1, 1, 3, cost=100)], resources=9_500, capacity=10_000,
                     production={1: 1000, 2: 1000, 3: 1000, 4: 1000})
    assert vb.overflow_recruitment(vb.plan_village(ready, 10, 0), phalanx, 1.0) == 0


def test_finishing_running_warehouse_saves_overflow():
    warehouse = _storage(30, 10, 4, capacity_step=1000)  # 4000 -> 5000
    granary = _storage(31, 11, 9, capacity_step=1000)
    village = _village([_field(1, 1, 3, cost=10)], [warehouse, granary], resources=3500,
                       capacity=4000, production={1: 1000, 2: 1000, 3: 1000, 4: 0}, free=0)
    village.capacity[4] = 100_000
    village.queue = [vb.QueueEntry(1, 30, 10, finished=7200)]  # 2 h left
    plan = vb.plan_village(village, 10, 0)
    saved = vb.storage_finish_saving(plan, 0)
    # Without finishing: 3500 + 2000 - 4000 = 1500 lost per resource (x3).
    # With +1000 capacity now: 500 per resource -> saves 3000.
    assert len(saved) == 1 and saved[0][1] == 3000


def test_reservable_master_entry_in_queue_order():
    warehouse = _storage(24, 10, 9)
    village = _village([_field(1, 1, 3, cost=10)], [warehouse], resources=1000, free=0)
    village.queue = [
        vb.QueueEntry(1, 1, 1, finished=500),
        vb.QueueEntry(4, 24, 10, 0, id="a", paid=True),
        vb.QueueEntry(4, 24, 10, 0, id="b", paid=False),
        vb.QueueEntry(4, 24, 10, 0, id="c", paid=False),
    ]
    entry, costs = vb.reservable_master_entry(village)
    assert entry.id == "b"  # the first unreserved one, never "c"
    village.resources = {r: 0 for r in vb.RESOURCES}
    assert vb.reservable_master_entry(village) is None


def test_choose_overflow_unit_wastes_least():
    village = _village([_field(1, 1, 3)])
    village.recruit_buildings = {19, 20}
    troops = {
        21: {"costs": {1: 85, 2: 100, 3: 50, 4: 0}, "building_types": [19, 29]},
        26: {"costs": {1: 300, 2: 380, 3: 440, 4: 0}, "building_types": [20, 30]},
        27: {"costs": {1: 750, 2: 370, 3: 220, 4: 0}, "building_types": [21]},  # no workshop
    }
    # Only iron overflows: the iron-heavy unit wastes the least.
    assert vb.choose_overflow_unit(village, troops, {1: 0, 2: 0, 3: 4400, 4: 0}) == 26
    # Everything overflows evenly-ish: the cheap phalanx fits best.
    assert vb.choose_overflow_unit(village, troops, {1: 850, 2: 1000, 3: 500, 4: 0}) == 21
    assert vb.choose_overflow_unit(village, troops, {1: 0, 2: 0, 3: 0, 4: 0}) is None


def test_storage_chain_finishes_queue_up_to_waiting_warehouse():
    warehouse = _storage(24, 10, 4, capacity_step=1000)  # 4000 -> 5000
    granary = _storage(25, 11, 9)
    field = _field(1, 1, 3, cost=10)
    village = _village([field], [warehouse, granary], resources=3500, capacity=4000,
                       production={1: 1000, 2: 1000, 3: 1000, 4: 0}, free=0)
    village.capacity[4] = 100_000
    warehouse.times = {lvl: 3600 for lvl in warehouse.times}  # 1 h build
    village.queue = [
        vb.QueueEntry(2, 1, 1, finished=1800),  # field: 30 min left -> 1 gold
        vb.QueueEntry(4, 24, 10, 0, id="w", paid=True),  # then the warehouse (1 h)
    ]
    plan = vb.plan_village(village, 10, 0)
    chain = vb.storage_chain(plan, 0, vb.FinishTiers())
    assert chain is not None and chain.first.location == 1
    assert chain.steps == 2 and chain.price == 2  # 1 gold each (< 2 h)
    # Natural: 1.5 h -> 3500 + 1500 - 4000 = 1000 lost per resource (x3);
    # with +1000 capacity: 0 lost -> saves 3000.
    assert chain.saved == 3000
    # An unreserved warehouse entry would not start: no chain.
    village.queue[1] = vb.QueueEntry(4, 24, 10, 0, id="w", paid=False)
    assert vb.storage_chain(vb.plan_village(village, 10, 0), 0, vb.FinishTiers()) is None


def test_queue_unblock_transfer_for_unreserved_master_entry():
    warehouse = _storage(24, 10, 9)
    warehouse.costs = {lvl: {1: 3000, 2: 4000, 3: 2000, 4: 0} for lvl in warehouse.costs}
    receiver = _village([_slow_field(1, 1, 3)], [warehouse], resources=1000, vid="1",
                        production={1: 100, 2: 100, 3: 100, 4: 100}, free=0)
    receiver.free_slots = {1: 0, 2: 0, 4: 0}
    receiver.busy_until = 1800  # queue frees in 30 min
    receiver.queue = [vb.QueueEntry(2, 1, 1, finished=1800),
                      vb.QueueEntry(4, 24, 10, 0, id="w", paid=False)]
    plan = vb.plan_village(receiver, 10, 0)
    entry, missing, hours = vb.blocking_deficit(plan)
    assert entry.id == "w" and hours == 0.5
    assert missing[2] == 4000 - 1000 - 50  # clay: cost - stock - 0.5 h production
    donor = _village([_slow_field(1, 1, 3)], resources=50_000, vid="2", y=1,
                     production={1: 1e4, 2: 1e4, 3: 1e4, 4: 1e4}, merchants=10, carry=2000)
    plans = [plan, vb.plan_village(donor, 10, 0)]
    transfers = vb.plan_transfers(plans, {3: 24}, 1)
    first = transfers[0]
    assert first.target_id == "1" and "kolejka stanie" in first.reason
    assert first.resources[2] == 2950


CONFIG = {
    8: {"costs": {"1": 500, "2": 440, "3": 380, "4": 1240}, "time": 160, "supply": 3,
        "requirements": [{"type": 2, "building": 4, "level": 5}]},
    5: {"costs": {"1": 520, "2": 380, "3": 290, "4": 90}, "time": 160, "supply": 4,
        "requirements": [{"type": 2, "building": 1, "level": 10}, {"type": 2, "building": 15, "level": 5}]},
    17: {"costs": {"1": 80, "2": 70, "3": 120, "4": 70}, "time": 10, "supply": 4,
         "requirements": [{"type": 2, "building": 15, "level": 3}, {"type": None, "building": None, "level": None}]},
}


def test_new_bonus_building_when_requirements_met():
    crop = _field(1, 4, 6, cost=10_000)
    village = _village([crop], production={1: 100, 2: 100, 3: 100, 4: 4000})
    village.empty_slots = [19, 20]
    village.type_levels = {4: 6}
    plan = vb.plan_village(village, 10, 0, construct_config=CONFIG, balanced=False)
    step = plan.next_step
    assert step.construct and step.type == 8 and step.location == 19
    assert "nowy budynek" in step.reason
    # No sawmill: woodcutter 10 and main building 5 are missing.
    assert not any(s.type == 5 for s in plan.steps)
    # Only once, even over several planned steps.
    assert sum(1 for s in plan.steps if s.construct and s.type == 8) == 1
    assert village.type_levels == {4: 6}  # the village data is not changed


def test_marketplace_construction_requirements():
    village = _village([_field(1, 1, 3)])
    village.empty_slots = [21]
    village.type_levels = {15: 2}
    assert vb.marketplace_construction(village, CONFIG, "x") is None  # main building 3 needed
    village.type_levels = {15: 4}
    step = vb.marketplace_construction(village, CONFIG, "x")
    assert step.construct and step.type == vb.MARKETPLACE and step.location == 21
    village.empty_slots = []
    assert vb.marketplace_construction(village, CONFIG, "x") is None


def _barracks(location=29, type_=19, level=14):
    return vb.Building(
        location=location, type=type_, level=level, max_level=20,
        costs={lvl: {r: 100 for r in vb.RESOURCES} for lvl in range(level, level + 7)},
        times={lvl: 3600 for lvl in range(level, level + 7)},
        effects={}, supply={lvl: lvl for lvl in range(level, level + 8)},
    )


def test_military_steps_alternate_when_village_has_spare():
    village = _village([_field(1, 1, 3, cost=10)])
    village.military = [_barracks()]
    plan = vb.plan_village(village, 10, 0, military={19: 20}, military_first=True, depth=4)
    assert [s.type for s in plan.steps] == [19, 1, 19, 1]
    assert "wojsko" in plan.steps[0].reason
    # Without spare resources the fields go first; barracks after them.
    plan = vb.plan_village(village, 4, 0, military={19: 20}, depth=3)
    assert [s.type for s in plan.steps] == [1, 19, 19]
    # Stops at the target level.
    village.military = [_barracks(level=19)]
    plan = vb.plan_village(village, 3, 0, military={19: 20}, depth=4)
    assert [s.type for s in plan.steps] == [19]


def test_donor_keeps_cost_of_its_waiting_master_entry():
    warehouse = _storage(24, 10, 9)
    warehouse.costs = {lvl: {1: 0, 2: 5000, 3: 0, 4: 0} for lvl in warehouse.costs}
    donor = _village([_slow_field(1, 1, 3)], [warehouse], resources=6000, vid="1",
                     production={1: 1e4, 2: 1e4, 3: 1e4, 4: 1e4}, merchants=10, carry=2000, free=0)
    donor.free_slots = {1: 0, 2: 0, 4: 0}
    donor.queue = [vb.QueueEntry(2, 1, 1, finished=600),
                   vb.QueueEntry(4, 24, 10, 0, id="w", paid=False)]
    plan = vb.plan_village(donor, 10, 0)
    spare = vb._spare(plan)
    assert spare[2] <= 6000 - 5000  # the warehouse's clay stays


def test_tiny_unblock_shortage_is_not_shipped():
    warehouse = _storage(24, 10, 9)
    warehouse.costs = {lvl: {1: 0, 2: 1010, 3: 0, 4: 0} for lvl in warehouse.costs}
    receiver = _village([_slow_field(1, 1, 3)], [warehouse], resources=1000, vid="1",
                        production={1: 1000, 2: 1000, 3: 1000, 4: 1000}, free=0)
    receiver.free_slots = {1: 0, 2: 0, 4: 0}
    receiver.queue = [vb.QueueEntry(4, 24, 10, 0, id="w", paid=False)]
    donor = _village([_slow_field(1, 1, 3)], resources=50_000, vid="2", y=1,
                     production={1: 1e4, 2: 1e4, 3: 1e4, 4: 1e4}, merchants=10, carry=2000)
    plans = [vb.plan_village(v, 10, 0) for v in (receiver, donor)]
    assert not any("kolejka stanie" in t.reason for t in vb.plan_transfers(plans, {3: 24}, 1))


def test_young_village_small_shortage_shipped_for_next_steps():
    warehouse = _storage(24, 10, 9)
    warehouse.costs = {lvl: {1: 0, 2: 250, 3: 0, 4: 0} for lvl in warehouse.costs}
    field = _field(1, 1, 3, cost=400)
    receiver = _village([field], [warehouse], resources=100, vid="1",
                        production={1: 100, 2: 100, 3: 100, 4: 100}, free=0)
    receiver.free_slots = {1: 0, 2: 0, 4: 0}
    receiver.queue = [vb.QueueEntry(4, 24, 10, 0, id="w", paid=False)]
    donor = _village([_slow_field(1, 1, 3)], resources=50_000, vid="2", y=1,
                     production={1: 1e4, 2: 1e4, 3: 1e4, 4: 1e4}, merchants=10, carry=2000)
    plans = [vb.plan_village(v, 10, 0) for v in (receiver, donor)]
    missing = vb.blocking_deficit(plans[0])[1]
    assert sum(missing.values()) < vb.UNBLOCK_MIN_SHIPMENT  # 150 clay: 1.5 h at 100/h
    shipped = [t for t in vb.plan_transfers(plans, {3: 24}, 1)
               if t.target_id == "1" and "u budowniczego" in t.reason]
    assert shipped and sum(t.total for t in shipped) >= vb.UNBLOCK_MIN_SHIPMENT


def test_build_reserve_includes_next_unreserved_master_entry():
    warehouse = _storage(24, 10, 9)
    warehouse.costs = {lvl: {1: 100, 2: 200, 3: 300, 4: 0} for lvl in warehouse.costs}
    village = _village([_field(1, 1, 3, cost=10)], [warehouse], free=0)
    village.queue = [vb.QueueEntry(4, 24, 10, 0, id="a", paid=True),
                     vb.QueueEntry(4, 24, 10, 0, id="b", paid=False),
                     vb.QueueEntry(4, 24, 10, 0, id="c", paid=False)]
    plan = vb.plan_village(village, 10, 0)
    reserve = vb.build_reserve(plan)
    step = plan.next_step.costs
    # Next step + only the first unreserved entry ("b"), not the paid one or "c".
    assert reserve == {r: step[r] + {1: 100, 2: 200, 3: 300, 4: 0}[r] for r in vb.RESOURCES}


def test_overflow_forecast_counts_running_granary():
    granary = _storage(25, 11, 9, capacity_step=1000)  # 9000 -> 10000
    warehouse = _storage(24, 10, 9, capacity_step=10_000)
    village = _village([_field(1, 1, 3)], [warehouse, granary], resources=8_500,
                       capacity=90_000, production={1: 0, 2: 0, 3: 0, 4: 500}, free=0)
    village.capacity[4] = 9_000
    village.busy_until = 1800
    village.queue = [vb.QueueEntry(1, 25, 11, finished=1800)]  # granary ends in 0.5 h
    plan = vb.plan_village(village, 10, 0)
    # 2 h: 8500 + 1000 = 9500 -> fits the bigger granary (10 000), and
    # before it ends (0.5 h) only 8750 is in stock.
    assert vb.overflow_excess(plan, 2.0)[4] == 0
    village.queue = []
    village.busy_until = 0
    assert vb.overflow_excess(vb.plan_village(village, 10, 0), 2.0)[4] == 500


STORAGE_CONFIG = {
    10: {"costs": {"1": 130, "2": 160, "3": 90, "4": 40}, "time": 2000, "supply": 1,
         "requirements": [{"type": 2, "building": 15, "level": 1}]},
    11: {"costs": {"1": 80, "2": 100, "3": 70, "4": 20}, "time": 1600, "supply": 1,
         "requirements": [{"type": 2, "building": 15, "level": 1}]},
}


def test_new_village_without_storage_builds_warehouse_first():
    # A field level costs more than the 800 a village without warehouse holds.
    village = _village([_field(1, 1, 5, cost=200)], capacity=800)
    village.empty_slots = [19, 39, 40, 20]
    village.type_levels = {15: 1}
    plan = vb.plan_village(village, 10, 0, construct_config=STORAGE_CONFIG, balanced=False)
    step = plan.next_step
    assert step.construct and step.type == vb.WAREHOUSE and step.location == 19
    assert "nowy magazyn" in step.reason
    # Planned once; nothing else claims its site.
    assert sum(1 for s in plan.steps if s.type == vb.WAREHOUSE) == 1
    assert village.empty_slots == [19, 39, 40, 20]


def test_storage_reserve_builds_missing_granary():
    village = _village([_field(1, 1, 5, cost=10)], capacity=800,
                       production={1: 100, 2: 100, 3: 100, 4: 500})
    village.storages = [_storage(25, 10, 3)]
    village.empty_slots = [26]
    village.type_levels = {15: 1, 10: 3}
    plan = vb.plan_village(village, 10, 0, construct_config=STORAGE_CONFIG, balanced=False,
                           storage_hours=12)
    granaries = [s for s in plan.steps if s.type == vb.GRANARY]
    assert len(granaries) == 1 and granaries[0].construct and granaries[0].location == 26


def test_no_new_storage_on_rally_point_or_wall_site():
    village = _village([_field(1, 1, 5, cost=200)], capacity=800)
    village.empty_slots = [39, 40]
    village.type_levels = {15: 1}
    plan = vb.plan_village(village, 10, 0, construct_config=STORAGE_CONFIG, balanced=False)
    assert plan.next_step is None and "brak budynku" in plan.blocked


def test_no_new_storage_without_main_building():
    village = _village([_field(1, 1, 5, cost=200)], capacity=800)
    village.empty_slots = [19]
    assert vb.storage_construction(village, 1, STORAGE_CONFIG) is None


def _stalled_receiver():
    warehouse = _storage(24, 10, 9)
    warehouse.costs = {lvl: {1: 0, 2: 4000, 3: 0, 4: 0} for lvl in warehouse.costs}
    receiver = _village([_slow_field(1, 1, 3)], [warehouse], resources=1000, vid="1",
                        production={1: 1000, 2: 1000, 3: 1000, 4: 1000}, free=0)
    receiver.free_slots = {1: 0, 2: 0, 4: 0}
    receiver.busy_until = 1800
    receiver.queue = [vb.QueueEntry(2, 1, 1, finished=1800),
                      vb.QueueEntry(4, 24, 10, 0, id="w", paid=False)]
    return vb.plan_village(receiver, 10, 0)


def _reserved_donor(clay_rate):
    # Its own next step needs everything it has: nothing is spare.
    field = _slow_field(1, 2, 3)
    field.costs = {lvl: {r: 5000 for r in vb.RESOURCES} for lvl in field.costs}
    donor = _village([field], resources=5000, vid="2", y=1, merchants=10, carry=2000,
                     production={1: 1000, 2: clay_rate, 3: 1000, 4: 1000})
    plan = vb.plan_village(donor, 10, 0)
    assert vb._spare(plan)[2] == 0
    return plan


def test_neighbour_lends_reserve_it_makes_back_quickly():
    receiver = _stalled_receiver()  # lacks 2500 clay: stands 2.5 h
    donor = _reserved_donor(clay_rate=10_000)  # makes 2500 clay in 15 min
    transfers = vb.plan_transfers([receiver, donor], {3: 24}, 1)
    loan = next(t for t in transfers if "pożyczka" in t.reason)
    assert loan.source_id == "2" and loan.target_id == "1"
    assert loan.resources == {2: 2500}


def _waiting_donor(missing_type):
    # The donor's own master builder entry waits for one resource.
    donor = _reserved_donor(clay_rate=10_000)
    warehouse = _storage(24, 10, 9)
    warehouse.costs = {lvl: {r: (9000 if r == missing_type else 0) for r in vb.RESOURCES}
                       for lvl in warehouse.costs}
    village = donor.village
    village.storages = [warehouse]
    village.free_slots = {1: 0, 2: 0, 4: 0}
    village.queue = [vb.QueueEntry(4, 24, 10, 0, id="d", paid=False)]
    plan = vb.plan_village(village, 10, 0)
    assert vb.blocking_deficit(plan) is not None
    return plan


def test_waiting_neighbour_lends_what_its_own_wait_does_not_need():
    receiver = _stalled_receiver()  # lacks clay
    transfers = vb.plan_transfers([receiver, _waiting_donor(missing_type=1)], {3: 24}, 1)
    assert any("pożyczka" in t.reason and t.resources.get(2) for t in transfers)  # waits for wood
    receiver = _stalled_receiver()
    transfers = vb.plan_transfers([receiver, _waiting_donor(missing_type=2)], {3: 24}, 1)
    assert not any("pożyczka" in t.reason for t in transfers)  # waits for clay itself


def test_no_loan_when_neighbour_produces_slowly():
    receiver = _stalled_receiver()
    donor = _reserved_donor(clay_rate=1000)  # as slow as the receiver
    transfers = vb.plan_transfers([receiver, donor], {3: 24}, 1)
    assert not any("pożyczka" in t.reason for t in transfers)


def _hero_plan(production, cost=1000):
    field = _field(1, 1, 3, cost=cost)
    field.costs = {lvl: {1: 1000, 2: 1000, 3: 1000, 4: 500} for lvl in field.costs}
    # Production stays as given while the field goes up.
    field.effects = {lvl: 30 for lvl in field.effects}
    return vb.plan_village(_village([field], production=production), 10, 0)


def test_hero_ignores_a_resource_whose_fields_are_about_to_grow():
    # Iron is the scarcest today, but its low fields will triple it on the
    # way to the target; clay stays flat and is the real bottleneck.
    iron = _field(1, 3, 3, gain_per_level=10)
    clay = _field(2, 2, 3)
    clay.effects = {lvl: 30 for lvl in clay.effects}
    for field in (iron, clay):
        field.costs = {lvl: {1: 1000, 2: 1000, 3: 1000, 4: 500} for lvl in field.costs}
    # Iron 6000 includes the hero's 4000: 2000 of its own today.
    village = _village([iron, clay], production={1: 9000, 2: 3000, 3: 6000, 4: 3000})
    plan = vb.plan_village(village, 10, 0)
    best, hours = vb.choose_hero_resource([plan], current=3, amount_all=1000)
    assert best == 2
    assert hours[2] < hours[3]


def test_hero_moves_off_the_abundant_resource():
    # Clay is plentiful (hero on clay adds 4000 of its 9000); wood is scarce.
    plan = _hero_plan({1: 2000, 2: 9000, 3: 4000, 4: 3000})
    best, hours = vb.choose_hero_resource([plan], current=2, amount_all=1000)
    assert best == 1
    assert hours[1] < hours[0] < hours[2]


def test_hero_all_resources_when_two_are_short():
    plan = _hero_plan({1: 2000, 2: 9000, 3: 2000, 4: 3000})
    best, _hours = vb.choose_hero_resource([plan], current=2, amount_all=1000)
    assert best == vb.HERO_ALL


def test_hero_keeps_setting_without_clear_gain():
    plan = _hero_plan({1: 6000, 2: 6000, 3: 6000, 4: 3000})
    assert vb.choose_hero_resource([plan], current=vb.HERO_ALL, amount_all=1000) is None
    done = _hero_plan({1: 2000, 2: 9000, 3: 4000, 4: 3000})
    done.village.fields[0].level = done.village.fields[0].max_level
    done = vb.plan_village(done.village, 10, 0)
    assert vb.choose_hero_resource([done], current=2, amount_all=1000) is None


def test_no_surplus_shipment_while_paid_master_queue_runs():
    # Gludio case: one build ends soon, but 4 paid master entries (1 h each)
    # run after it; the village makes its next steps' resources itself.
    field = _field(1, 1, 3, cost=1000)
    field.times = {lvl: 3600 for lvl in field.times}
    receiver = _village([field], resources=3_000, capacity=50_000, vid="1",
                        production={1: 1500, 2: 1500, 3: 1500, 4: 1500}, free=0)
    receiver.free_slots = {1: 0, 2: 0, 4: 0}
    receiver.busy_until = 360
    receiver.queue = [vb.QueueEntry(2, 1, 1, finished=360)] + [
        vb.QueueEntry(4, 1, 1, 0, id=str(i), paid=True) for i in range(4)
    ]
    donor = _village([], resources=50_000, vid="2", y=1, merchants=10, carry=1000)
    plans = [vb.plan_village(v, 10, 0) for v in (receiver, donor)]
    assert not plans[0].queue_bound
    assert vb.idle_hours(plans[0]) > 4
    assert not [t for t in vb.plan_transfers(plans, {3: 24}, 1) if t.target_id == "1"]
    # Once the queue frees, the surplus goes there again.
    receiver.queue = [vb.QueueEntry(2, 1, 1, finished=360)]
    plans = [vb.plan_village(v, 10, 0) for v in (receiver, donor)]
    assert [t for t in vb.plan_transfers(plans, {3: 24}, 1) if t.target_id == "1"]


# Raid protection ---------------------------------------------------------------




def _raid_villages(resources=20_000, receiver_resources=0, receiver_capacity=100_000):
    donor = _village([_field(1, 1, 3)], resources=resources, vid="1", merchants=5, carry=2000)
    donor.marketplace = _market(location=30, level=10)
    near = _village([_field(1, 1, 3)], resources=receiver_resources, capacity=receiver_capacity,
                    vid="2", x=3, production={1: 0, 2: 0, 3: 0, 4: 0})
    far = _village([_field(1, 1, 3)], resources=0, vid="3", x=30,
                   production={1: 0, 2: 0, 3: 0, 4: 0})
    return [vb.plan_village(v, 10, 0) for v in (donor, near, far)]


def test_raid_evacuation_sends_lootable_stock_to_nearest_safe_village():
    plans = _raid_villages()
    evacuations = vb.plan_evacuation(plans, {"1"}, {"1"}, {3: 24}, 1)
    assert [e.target_id for e in evacuations] == ["2"]
    # 5 merchants x 2000 = 10 000: wood, clay and iron before crop.
    assert evacuations[0].resources == {1: 3333, 2: 3333, 3: 3333, 4: 1}
    assert evacuations[0].merchants == 5


def test_raid_evacuation_skips_villages_under_attack_and_fills_by_room():
    plans = _raid_villages(receiver_resources=98_000)  # near: 2000 room each
    evacuations = vb.plan_evacuation(plans, {"1"}, {"1"}, {3: 24}, 1)
    assert [e.target_id for e in evacuations] == ["2", "3"]
    assert evacuations[0].resources == {r: 2000 for r in vb.RESOURCES}  # 4 merchants
    assert sum(evacuations[1].resources.values()) == 2000  # the last merchant
    plans = _raid_villages()
    evacuations = vb.plan_evacuation(plans, {"1"}, {"1", "2"}, {3: 24}, 1)
    assert [e.target_id for e in evacuations] == ["3"]


def test_cranny_and_crop_buffer_stay_home():
    village = _village([_field(1, 1, 3)], resources=5000,
                       production={1: 100, 2: 100, 3: 100, 4: -1000})
    village.protected = 1000
    assert vb.raid_exposed(village) == {1: 4000, 2: 4000, 3: 4000, 4: 3000}


def test_raid_recruitment_limited_by_scarcest_cost():
    assert vb.raid_recruitment({1: 1000, 2: 1000, 3: 50, 4: 1000}, {1: 100, 2: 130, 3: 55, 4: 30}) == 0
    assert vb.raid_recruitment({1: 1000, 2: 1000, 3: 500, 4: 1000}, {1: 100, 2: 130, 3: 55, 4: 30}) == 7


def test_no_shipment_that_lands_just_before_an_attack():
    threats = {"2": 10_000.0}
    assert vb.shipment_raided(threats, "2", 0, 1.0)  # lands at 3600, attack 10000
    assert not vb.shipment_raided(threats, "2", 0, 3.0)  # lands after the attack
    assert not vb.shipment_raided(threats, "3", 0, 1.0)


def test_hostile_arrivals_ignore_scouts_and_past_attacks():
    from src.core.build_automation import hostile_arrivals

    attacks = [
        {"village_id": "1", "arrival_time": 500, "movement_type": "Zwiad"},
        {"village_id": "1", "arrival_time": 900, "movement_type": "Napad"},
        {"village_id": "1", "arrival_time": 700, "movement_type": "Atak"},
        {"village_id": "2", "arrival_time": 50, "movement_type": "Atak"},
    ]
    assert hostile_arrivals(attacks, 100) == {"1": 700.0}


class _RaidExecutor:
    def __init__(self):
        self.sent = []
        self.recruited = []

    def send_resources(self, source_id, location, name, x, y, amounts):
        self.sent.append((source_id, name, dict(amounts)))
        return dict(amounts)

    def recruit(self, village_id, building_types, unit, count, *args):
        self.recruited.append((village_id, unit, count))
        return {"status": "confirmed", "quantity": count, "count": count}


def test_protect_ships_then_trains_what_cannot_leave():
    from src.config.settings import BuildSettings
    from src.core.build_automation import BuildAutomation, BuildSnapshot

    settings = BuildSettings(enabled=True, overflow_units={"1": 21})
    executor = _RaidExecutor()
    logs = []
    automation = BuildAutomation(None, lambda: settings, logs.append, executor=executor)
    plans = _raid_villages()
    plans[0].village.recruit_buildings = {19}
    snapshot = BuildSnapshot(
        plans=plans, server_time=0, threats={"1": 300.0},
        merchant_speed={3: 24}, world_speed=1,
        troops={21: {"costs": {1: 100, 2: 130, 3: 55, 4: 30}, "building_types": [19]}},
    )
    assert automation._protect(snapshot, settings, {"1": 300.0})
    assert executor.sent == [("1", "V2", {1: 3333, 2: 3333, 3: 3333, 4: 1})]
    # Left: 16 667 of wood, clay and iron -> clay limits: 16 667 // 130 = 128.
    assert executor.recruited == [("1", 21, 128)]
    assert any("atak na V1 za 5 min" in line for line in logs)


# Crop bank -----------------------------------------------------------------------


def _granary(location, level, max_level=20):
    building = _storage(location, 11, level)
    building.max_level = max_level
    building.costs = {lvl: {1: 100, 2: 100, 3: 100, 4: 10} for lvl in range(level, max_level)}
    building.times = {lvl: 600 for lvl in range(level, max_level)}
    building.effects = {lvl: 1000.0 * lvl for lvl in range(level, max_level + 1)}
    building.supply = {lvl: lvl for lvl in range(level, max_level + 1)}
    return building


def test_crop_bank_raises_granaries_before_anything_else():
    village = _village([_field(1, 1, 3)], [_granary(25, 18), _granary(34, 18)], capital=True)
    plan = vb.plan_village(village, 10, 0, granary_max=True)
    kinds = [s.type for s in plan.steps[:6]]
    assert kinds[:4] == [11, 11, 11, 11]  # 2 granaries x 2 levels, then the rest
    assert kinds[4] != 11
    assert "bank zboża" in plan.steps[0].reason
    plain = vb.plan_village(village, 10, 0)
    assert all(s.type != 11 for s in plain.steps)


def test_crop_bank_never_builds_a_new_granary():
    village = _village([_field(1, 1, 3)], [_granary(25, 20)], capital=True)
    village.empty_slots = [30]
    plan = vb.plan_village(village, 10, 0, granary_max=True, construct_config={11: {
        "costs": {"1": 80, "2": 100, "3": 70, "4": 20}, "time": 10, "supply": 1, "requirements": [],
    }})
    assert all(s.type != 11 for s in plan.steps)


def _bank_plans(bank_crop=99_500, warehouse_stock=10_000):
    bank = _village([_field(1, 1, 3, cost=10)], capital=True, vid="1",
                    production={1: 1000, 2: 1000, 3: 1000, 4: 1000})
    bank.capacity = {1: 100_000, 2: 100_000, 3: 100_000, 4: 100_000}
    bank.resources = {1: warehouse_stock, 2: warehouse_stock, 3: warehouse_stock, 4: bank_crop}
    other = _village([_field(1, 2, 3, cost=5_000_000)], vid="2", x=3, resources=0,
                     production={1: 0, 2: 0, 3: 0, 4: 0})
    return [vb.plan_village(v, 10, 0) for v in (bank, other)]


def test_mill_waits_until_granary_is_nearly_full():
    plans = _bank_plans(bank_crop=90_000)
    assert vb.crop_bank_mill(plans[0], plans, 0.99, 30_000) is None


def test_mill_converts_crop_into_what_the_network_lacks():
    plans = _bank_plans()
    gains = vb.crop_bank_mill(plans[0], plans, 0.99, 30_000)
    # The other village's field needs equal wood, clay and iron: an even
    # split, capped by the free warehouse (100k - 10k - 1 h of production).
    assert gains is not None and set(gains) == {1, 2, 3}
    assert max(gains.values()) - min(gains.values()) <= 2
    assert all(amount <= 89_000 for amount in gains.values())
    keep = vb.build_reserve(plans[0])[4]
    assert abs(sum(gains.values()) - (99_500 - keep)) <= 3


def test_mill_limited_by_free_warehouse():
    plans = _bank_plans(warehouse_stock=95_000)  # 4 000 free each after 1 h
    gains = vb.crop_bank_mill(plans[0], plans, 0.99, 1_000)
    assert gains == {1: 4000, 2: 4000, 3: 4000}
    assert vb.crop_bank_mill(plans[0], plans, 0.99, 30_000) is None


def test_spare_crop_goes_to_the_bank_and_bank_keeps_its_crop():
    bank = _village([_field(1, 1, 3, cost=10)], capital=True, vid="1", merchants=10, carry=2000,
                    production={1: 0, 2: 0, 3: 0, 4: 0}, resources=50_000)
    bank.capacity = {r: 100_000 for r in vb.RESOURCES}
    bank.resources[4] = 99_000  # would overflow: but the bank mills instead
    bank.production = {1: 0, 2: 0, 3: 0, 4: 5_000}
    donor = _village([_slow_field(1, 1, 3)], vid="2", x=3, merchants=10, carry=2000,
                     resources=20_000, production={1: 1e4, 2: 1e4, 3: 1e4, 4: 1e4})
    plans = [vb.plan_village(v, 10, 0) for v in (bank, donor)]
    plans[0].village.capacity[4] = 200_000
    transfers = vb.plan_transfers(plans, {3: 24}, 1, min_surplus_shipment=1000, crop_bank="1")
    to_bank = [t for t in transfers if t.target_id == "1" and "banku" in t.reason]
    assert to_bank and set(to_bank[0].resources) == {4}
    assert not any(t.source_id == "1" and 4 in t.resources for t in transfers)


class _MillExecutor:
    def __init__(self):
        self.calls = []

    def npc_convert(self, village_id, location, gains, price):
        self.calls.append((village_id, dict(gains), price))
        return {"spent": price, "gains": dict(gains), "crop": sum(gains.values())}


def test_mill_clicks_npc_and_keeps_gold_reserve():
    from src.config.settings import BuildSettings
    from src.core.build_automation import BuildAutomation, BuildSnapshot

    settings = BuildSettings(enabled=True, crop_bank=True, gold_reserve=50)
    executor = _MillExecutor()
    logs = []
    automation = BuildAutomation(None, lambda: settings, logs.append, executor=executor)
    plans = _bank_plans()
    plans[0].village.marketplace = _market(location=20, level=10)
    snapshot = BuildSnapshot(plans=plans, crop_bank="1", gold=54, npc_price=5)
    assert not automation._mill(snapshot, settings) and not executor.calls  # 54 - 5 < 50
    snapshot.gold = 100
    assert automation._mill(snapshot, settings)
    assert executor.calls[0][0] == "1" and executor.calls[0][2] == 5
    assert settings.crop_bank_gold_spent == 5 and automation.budget_changed
    assert any("Bank zboża" in line for line in logs)


def test_crop_bank_grows_warehouse_with_granaries():
    warehouse = _storage(24, 10, 10)
    warehouse.max_level = 20
    warehouse.costs = {lvl: {1: 100, 2: 100, 3: 100, 4: 0} for lvl in range(10, 20)}
    warehouse.times = {lvl: 600 for lvl in range(10, 20)}
    warehouse.effects = {lvl: 1000.0 * lvl for lvl in range(10, 21)}
    warehouse.supply = {lvl: lvl for lvl in range(10, 21)}
    village = _village([_field(1, 1, 3)], [warehouse, _granary(25, 18)], capital=True,
                       production={1: 100, 2: 100, 3: 100, 4: 100})
    village.capacity = {1: 10_000, 2: 10_000, 3: 10_000, 4: 60_000}
    plan = vb.plan_village(village, 10, 0, granary_max=True)
    # 60 000 / 3 + 2 h x 100 = 20 200 > 10 000: the warehouse first.
    assert plan.steps[0].type == 10 and "magazyn na zmielone" in plan.steps[0].reason


def test_crop_bank_master_builder_drops_entry_in_front_of_granaries():
    warehouse = _storage(24, 10, 17)
    warehouse.max_level = 20
    warehouse.costs = {lvl: {1: 100, 2: 100, 3: 100, 4: 0} for lvl in range(17, 20)}
    warehouse.times = {lvl: 600 for lvl in range(17, 20)}
    warehouse.effects = {lvl: 10_000.0 * lvl for lvl in range(17, 21)}
    warehouse.supply = {lvl: lvl for lvl in range(17, 21)}
    warehouse.queued = 1
    village = _village([_field(1, 1, 3)], [warehouse, _granary(25, 14), _granary(34, 14)], capital=True,
                       production={1: 100, 2: 100, 3: 100, 4: 100})
    village.capacity = {1: 170_000, 2: 170_000, 3: 170_000, 4: 28_000}
    village.queue = [vb.QueueEntry(4, 24, 10, 0, id="w", paid=False)]
    assert [e.id for e in vb.master_replacements(village, 10, 0, 4, granary_max=True)] == ["w"]
    # Without the bank the plan does not want that warehouse at all: out too.
    assert [e.id for e in vb.master_replacements(village, 10, 0, 4)] == ["w"]


def test_mill_waits_for_a_granary_finishing_before_overflow():
    plans = _bank_plans()  # 99 500 / 100 000, +1000 crop/h: full in 30 min
    bank = plans[0].village
    bank.queue = [vb.QueueEntry(1, 25, 11, finished=10 * 60)]  # bigger granary in 10 min
    assert vb.crop_bank_mill(plans[0], plans, 0.99, 30_000, server_time=0) is None
    bank.queue = [vb.QueueEntry(1, 25, 11, finished=3 * 3600)]  # too late: mill now
    assert vb.crop_bank_mill(plans[0], plans, 0.99, 30_000, server_time=0) is not None


def test_mill_covers_near_needs_first():
    plans = _bank_plans()
    bank = plans[0].village
    bank.marketplace = _market(location=20, level=20)
    bank.merchant_carry = 2000
    # The neighbour's next step lacks 40 000 clay; the network as a whole
    # needs wood, clay and iron equally.
    neighbour = plans[1]
    neighbour.steps = [vb.Step("2", 1, 2, 3, {1: 0, 2: 40_000, 3: 0, 4: 0}, 3600, 1.0, 10.0, "")]
    gains = vb.crop_bank_mill(plans[0], plans, 0.99, 30_000, 0, {3: 24}, 1)
    assert gains[2] >= 40_000 and gains[2] > gains[1] and gains[2] > gains[3]


def test_master_builder_lines_up_steps_put_in_front():
    village = _village([_field(1, 1, 3)], free=0)
    village.empty_slots = [30]
    market = vb.Step("1", 30, vb.MARKETPLACE, 0, {r: 10 for r in vb.RESOURCES}, 60, 0.0, None,
                     "nowe targowisko", construct=True)
    step = vb.master_next_step(village, 10, 0, 4, first_steps=[market])
    assert step is market
    assert vb.master_next_step(village, 10, 0, 4).type == 1


def test_marketplace_grows_to_carry_crop_to_the_bank():
    bank = _village([_field(1, 1, 3)], capital=True, vid="1")
    far = _village([_slow_field(1, 1, 3)], vid="2", y=18, resources=15_000, merchants=1, carry=1125,
                   production={1: 1000, 2: 1000, 3: 1000, 4: 1200})
    far.marketplace = _market(location=20, level=1)
    plan = vb.plan_village(far, 10, 0)
    # 36 fields at 24/h: 1.5 h round trip; ~15k spare + 4.8k in 4 h -> 7 merchants.
    assert vb.bank_merchants_missing(plan, bank, {3: 24}, 1) >= 5
    far.marketplace.level = 20
    assert vb.bank_merchants_missing(plan, bank, {3: 24}, 1) == 0


def _stalled_home(clay_missing=3400, free=1):
    field = _field(1, 1, 3)
    field.costs = {lvl: {1: 0, 2: 10_000, 3: 0, 4: 0} for lvl in field.costs}
    village = _village([field], vid="1", resources=10_000 - clay_missing, free=free,
                       production={1: 9000, 2: 9400, 3: 9000, 4: 6000})
    return vb.plan_village(village, 10, 0)


def test_hero_produces_what_a_stalled_queue_waits_for():
    plan = _stalled_home()
    best, before, after = vb.hero_stall_choice(plan, vb.HERO_ALL, 1080)
    assert best == 2 and after < before  # clay
    assert vb.hero_stall_choice(_stalled_home(free=0), vb.HERO_ALL, 1080) is None  # queue busy
    assert vb.hero_stall_choice(_stalled_home(clay_missing=200), vb.HERO_ALL, 1080) is None  # < 3 min


class _HeroExecutor:
    def __init__(self):
        self.choices = []

    def set_hero_production(self, choice):
        self.choices.append(choice)
        return {"all": 1080, "single": 4320}


def test_hero_returns_after_the_stall():
    from src.config.settings import BuildSettings
    from src.core.build_automation import BuildAutomation, BuildSnapshot

    settings = BuildSettings(enabled=True)
    executor = _HeroExecutor()
    automation = BuildAutomation(None, lambda: settings, lambda _t: None, executor=executor)
    plan = _stalled_home()
    hero = {"village_id": "1", "type": vb.HERO_ALL, "points": 68, "dead": False}
    assert automation._hero(BuildSnapshot(plans=[plan], hero=hero))
    assert executor.choices == [2]
    hero = dict(hero, type=2)
    assert not automation._hero(BuildSnapshot(plans=[plan], hero=hero))  # still waiting
    busy = _stalled_home(free=0)
    automation._hero(BuildSnapshot(plans=[busy], hero=hero))  # building again
    assert not automation._hero_for_stall or executor.choices[-1] != 2


def test_level_tables_sent_as_lists_for_a_new_building():
    raw = {"locationId": 20, "buildingType": 17, "lvl": 0, "lvlMax": 20,
           "nextUpgradeCosts": [{"1": 77, "2": 67, "3": 116, "4": 67}, {"1": 97, "2": 87, "3": 150, "4": 87}],
           "nextUpgradeTimes": [9, 70], "upgradeSupplyUsageSums": [0, 4], "effect": [0, 1]}
    building = vb._building_from_raw(raw)
    assert building.costs[0] == {1: 77, 2: 67, 3: 116, 4: 67} and building.costs[1][3] == 150
    assert building.times == {0: 9, 1: 70} and building.supply == {0: 0, 1: 4}


def test_hero_kept_for_a_stall_after_restart():
    from src.config.settings import BuildSettings
    from src.core.build_automation import BuildAutomation, BuildSnapshot

    executor = _HeroExecutor()
    automation = BuildAutomation(None, lambda: BuildSettings(enabled=True), lambda _t: None, executor=executor)
    hero = {"village_id": "1", "type": 2, "points": 68, "dead": False}  # clay, set before restart
    assert not automation._hero(BuildSnapshot(plans=[_stalled_home()], hero=hero))
    assert executor.choices == []


def test_mill_levels_the_slowest_resources_first():
    plans = _bank_plans()
    # Clay is produced 5x faster: its shortage takes the fewest hours.
    plans[0].village.production = {1: 1000, 2: 5000, 3: 1000, 4: 1000}
    gains = vb.crop_bank_mill(plans[0], plans, 0.99, 30_000)
    assert gains[2] < gains[1] and gains[2] < gains[3]


def test_stalled_master_entry_gives_way_to_what_can_start():
    warehouse = _storage(24, 10, 10)
    warehouse.costs = {lvl: {1: 9000, 2: 9000, 3: 9000, 4: 0} for lvl in warehouse.costs}
    village = _village([_field(1, 2, 3, cost=100)], [warehouse], resources=5000, free=0,
                       production={1: 1000, 2: 1000, 3: 1000, 4: 1000})
    village.free_slots = {1: 1, 2: 1, 4: 2}
    village.queue = [vb.QueueEntry(4, 24, 10, 0, id="w", paid=False),
                     vb.QueueEntry(4, 1, 2, 0, id="c", paid=False)]
    plan = vb.plan_village(village, 10, 0)
    entry, hours = vb.stalled_master_entry(plan)
    assert entry.id == "w" and abs(hours - 4.0) < 1e-6  # 4000 missing at 1000/h
    village.resources = {r: 8_800 for r in vb.RESOURCES}  # only ~12 min short
    assert vb.stalled_master_entry(vb.plan_village(village, 10, 0)) is None


def test_stalled_master_entry_kept_when_only_a_new_building_is_cheap():
    warehouse = _storage(24, 10, 10)
    warehouse.costs = {lvl: {1: 9000, 2: 9000, 3: 9000, 4: 0} for lvl in warehouse.costs}
    village = _village([], [warehouse], resources=5000, free=0,
                       production={1: 1000, 2: 1000, 3: 1000, 4: 1000})
    village.free_slots = {1: 1, 2: 1, 4: 3}
    village.queue = [vb.QueueEntry(4, 24, 10, 0, id="w", paid=False)]
    plan = vb.plan_village(village, 10, 0)
    cheap = vb.Step(village.id, 30, 5, 0, {1: 100, 2: 100, 3: 100, 4: 0}, 60, 1.0, None, "", construct=True)
    plan.steps = [plan.steps[0], cheap] if plan.steps else [cheap]
    # The new building may lack requirements: the entry is not given up for it.
    assert vb.stalled_master_entry(plan) is None


class _SendExecutor:
    def __init__(self):
        self.sent = []

    def send_resources(self, source_id, location, target_name, x, y, resources):
        self.sent.append((source_id, dict(resources)))
        return dict(resources)


def test_village_holding_stock_after_giving_up_an_entry_sends_nothing():
    import time
    from src.config.settings import BuildSettings
    from src.core.build_automation import BuildAutomation, BuildSnapshot

    source = _village([], vid="1", merchants=5, carry=1000)
    source.marketplace = _storage(30, 17, 3)
    target = _village([], vid="2", y=5)
    plans = [vb.plan_village(v, 10, 0) for v in (source, target)]
    transfer = vb.Transfer("1", "V1", "2", "V2", {4: 2000}, 2, 0.5, 0.0, 0.0, None, "nadwyżka")
    executor = _SendExecutor()
    automation = BuildAutomation(None, lambda: BuildSettings(enabled=True), lambda _t: None, executor=executor)
    automation._backoff["hold:1"] = time.monotonic() + 300
    assert not automation._send(BuildSnapshot(plans=plans, transfers=[transfer]), BuildSettings(enabled=True))
    assert executor.sent == []
    automation._backoff.clear()
    assert automation._send(BuildSnapshot(plans=plans, transfers=[transfer]), BuildSettings(enabled=True))
    assert executor.sent == [("1", {4: 2000})]


def test_storage_not_sized_for_a_temporary_hero_switch():
    village = _village([_field(1, 1, 3)], [_storage(24, 10, 10)], capacity=70_000,
                       production={1: 3500, 2: 8800, 3: 3400, 4: 5000})
    assert vb.plan_village(village, 10, 0, storage_hours=12).steps[0].type == 10  # 8 800 x 12 > 70 000
    village.hero_excess = {1: -1080, 2: 3240, 3: -1080, 4: -1080}  # hero on clay
    assert vb.plan_village(village, 10, 0, storage_hours=12).steps[0].type != 10  # 5 560 x 12 < 70 000


# Settlers ---------------------------------------------------------------------------

RESIDENCE_CONFIG = {25: {"costs": {"1": 580, "2": 460, "3": 350, "4": 180}, "time": 450, "supply": 1,
                         "requirements": [{"type": 2, "building": 15, "level": 5}]}}


def _settle_village(main_level=1):
    village = _village([_field(1, 1, 3)], vid="5", production={1: 1000, 2: 1000, 3: 1000, 4: 1000})
    village.main_building = _main_building(level=main_level, effects=(100, 96, 93, 90, 86, 83, 80, 78))
    village.empty_slots = [19, 20]
    village.settler_unit = 30
    village.settler_costs = {1: 3000, 2: 4000, 3: 3000, 4: 0}
    village.settler_seconds = 11_350
    return village


def test_settlers_main_building_then_residence_first():
    village = _settle_village()
    plan = vb.plan_village(village, 10, 0, construct_config=RESIDENCE_CONFIG, settle_level=10, settle_count=3)
    kinds = [(s.type, s.from_level, s.construct) for s in plan.steps[:5]]
    assert kinds == [(15, 1, False), (15, 2, False), (15, 3, False), (15, 4, False), (25, 0, True)]
    assert "osadnicy" in plan.steps[0].reason


def test_settlers_hold_the_village_until_trained():
    village = _settle_village(main_level=5)
    residence = _storage(19, 25, 10)
    village.residence = residence
    plan = vb.plan_village(village, 10, 0, settle_level=10, settle_count=3)
    assert len(plan.steps) == 1 and plan.steps[0].train
    assert plan.steps[0].costs == {1: 9000, 2: 12000, 3: 9000, 4: 0}
    assert vb.master_next_step(village, 10, 0, 4, settle_level=10, settle_count=3) is None
    village.settlers = 3
    plan = vb.plan_village(village, 10, 0, settle_level=10, settle_count=3)
    assert not any(s.train for s in plan.steps)


def test_settlers_served_first_by_transfers():
    settle = _settle_village(main_level=5)
    settle.residence = _storage(19, 25, 10)
    settle.resources = {r: 0 for r in vb.RESOURCES}
    other = _village([_field(1, 1, 3, cost=1000)], resources=0, vid="1", x=0, y=12)
    donor = _village([_field(1, 1, 9, max_level=10)], resources=50_000, vid="2", x=0, y=24,
                     merchants=5, carry=2000)
    plans = [vb.plan_village(other, 10, 0), vb.plan_village(donor, 10, 0),
             vb.plan_village(settle, 10, 0, settle_level=10, settle_count=3)]
    transfers = vb.plan_transfers(plans, {3: 24}, 1)
    assert transfers and transfers[0].target_id == "5" and transfers[0].merchants == 5


HAEDUAN = {"costs": {"1": 300, "2": 380, "3": 440, "4": 0}}
PHALANX = {"costs": {"1": 85, "2": 100, "3": 50, "4": 0}}


def _army_automation(**overrides):
    from src.config.settings import BuildSettings
    from src.core.build_automation import BuildAutomation

    settings = BuildSettings(enabled=True, military_villages=[
        {"village_id": "1", "units": {"19": 21, "20": 26}, "target_level": 20}], **overrides)
    village = _village([_field(1, 1, 3)], production={1: 6000, 2: 6000, 3: 6000, 4: 4000})
    village.recruit_buildings = {19, 20}
    village.unit_times = {21: 99, 26: 546}
    automation = BuildAutomation(None, lambda: settings, lambda text: None, executor=object())
    automation._military_active = list(settings.military_villages)
    return automation, settings, village


def test_continuous_training_rates():
    automation, settings, village = _army_automation()
    village.production = {1: 7000, 2: 7000, 3: 7000, 4: 4000}
    rates = automation._continuous_rates([village], {"26": HAEDUAN, "21": PHALANX}, settings)["1"]
    # Stable at full speed: 3600 / 546 haeduans per hour; barracks at 25 %.
    assert abs(rates[20][2] - 380 * 3600 / 546) < 1
    assert abs(rates[19][1] - 85 * 3600 / 99 * 0.25) < 1
    # Both (~9 500/h) under 50 % of 21 000/h production: not scaled down.
    assert sum(sum(r.values()) for r in rates.values()) < 10_500


def test_continuous_training_capped_and_braked():
    automation, settings, village = _army_automation(military_max_percent=20)
    rates = automation._continuous_rates([village], {"26": HAEDUAN, "21": PHALANX}, settings)["1"]
    assert abs(sum(sum(r.values()) for r in rates.values()) - 3600) < 1  # 20 % of 18 000
    village.production[4] = 1000  # network crop under the 1 500/h floor
    assert automation._continuous_rates([village], {"26": HAEDUAN}, settings) == {}


def test_settle_hours_counts_build_and_training():
    village = _settle_village()
    hours = vb.settle_hours(village, 10, 3, 11_350)
    # main building 4 x 900 s + residence 0->10 (~30 500 s x 0.86)
    # + 3 x 11 350 s x 0.9^9 (residence 10 trains faster)
    assert 9 < hours < 12


def test_settle_goal_starts_early_enough():
    from src.config.settings import BuildSettings
    from src.core.build_automation import BuildAutomation

    village = _settle_village()
    logs = []
    settings = BuildSettings(enabled=True, settle_village="5", settle_ready_at=100 * 3600)
    automation = BuildAutomation(None, lambda: settings, logs.append, executor=object())
    assert automation._settle_goal([village], settings, 0) == {}  # 100 h ahead: not yet
    assert automation._settle_goal([village], settings, 90 * 3600) == {"5": (10, 3)}
    assert any("osadnicy" in line for line in logs)


# Troop evasion ------------------------------------------------------------------------


class _EvadeExecutor:
    def __init__(self):
        self.sent = []
        self.recalled = []

    def send_troops(self, source_id, x, y, movement_type, units):
        self.sent.append((source_id, x, y, movement_type, dict(units)))
        return dict(units)

    def recall_troops(self, village_id, location_id, units):
        self.recalled.append((village_id, location_id, dict(units)))
        return dict(units)


def test_troops_leave_before_the_attack_and_come_back_after():
    from src.config.settings import BuildSettings
    from src.core.build_automation import BuildAutomation, BuildSnapshot

    settings = BuildSettings(enabled=True, troop_evasion_seconds=90)
    executor = _EvadeExecutor()
    automation = BuildAutomation(None, lambda: settings, lambda _t: None, executor=executor)
    home = _village([_field(1, 1, 3)], vid="1")
    home.units_home = {1: 96, 6: 9}
    near = _village([_field(1, 1, 3)], vid="2", x=1)
    far = _village([_field(1, 1, 3)], vid="3", x=20)
    plans = [vb.plan_village(v, 10, 0) for v in (home, near, far)]
    snap = lambda now, threats: BuildSnapshot(
        plans=plans, server_time=now, threats=threats, threats_last=threats, unit_speeds={1: 14, 6: 38})
    assert not automation._evade(snap(0, {"1": 300.0}), settings)  # 5 min ahead: not yet
    assert automation._evade(snap(220, {"1": 300.0}), settings)  # 80 s ahead: leave
    assert executor.sent == [("1", 1, 0, 5, {1: 96, 6: 9})]  # nearest safe village
    assert not automation._evade(snap(305, {}), settings)  # landed, troops still travelling
    home.units_elsewhere = [{"troop_id": 7, "location": "2", "units": {"1": 96, "6": 9}}]
    assert automation._evade(snap(320, {}), settings)
    assert executor.recalled == [("1", "2", {1: 96, 6: 9})]


def test_wave_end_keeps_troops_away_for_close_attacks():
    from src.core.build_automation import wave_ends

    attacks = [
        {"village_id": "1", "arrival_time": 100, "movement_type": "Atak"},
        {"village_id": "1", "arrival_time": 200, "movement_type": "Napad"},
        {"village_id": "1", "arrival_time": 1000, "movement_type": "Atak"},
        {"village_id": "1", "arrival_time": 150, "movement_type": "Zwiad"},
    ]
    assert wave_ends(attacks, 0) == {"1": 200}


def test_raid_lead_counts_merchant_trips():
    plans = _raid_villages(resources=60_000)  # marketplace 10: 10 x 2000 = 20 000 per trip
    donor = plans[0].village
    lead = vb.raid_lead_seconds(donor, [p.village for p in plans], {"1"}, {3: 24}, 1, 600, 3600)
    # 240 000 lootable -> 12 trips; 3 fields at 24/h -> 15 min round trip: capped at 1 h.
    assert lead == 3600
    donor.resources = {r: 3000 for r in vb.RESOURCES}  # 12 000 -> 1 trip -> 15 min + 2
    assert vb.raid_lead_seconds(donor, [p.village for p in plans], {"1"}, {3: 24}, 1, 600, 3600) == 900 + 120


def test_idle_queue_takes_an_affordable_later_step():
    expensive = _field(1, 3, 3)
    expensive.costs = {lvl: {1: 9801, 2: 7837, 3: 2939, 4: 5878} for lvl in expensive.costs}
    cheap = _field(2, 2, 3)
    cheap.costs = {lvl: {1: 7837, 2: 3918, 3: 7837, 4: 4898} for lvl in cheap.costs}
    village = _village([expensive, cheap], resources=9000, production={1: 2600, 2: 2600, 3: 2000, 4: 2500})
    village.resources = {1: 9036, 2: 19155, 3: 43728, 4: 15041}
    plan = vb.plan_village(village, 10, 0)
    plan.steps = [s for s in plan.steps if s.location == 1][:1] + [s for s in plan.steps if s.location == 2][:1]
    plan.afford_hours = 0.3
    assert vb.affordable_alternative(plan).location == 2
    plan.afford_hours = 0.1  # a few minutes: just wait
    assert vb.affordable_alternative(plan) is None


def test_idle_alternative_found_below_entries_given_up_for_it():
    # Given-up master entries return to the top of the plan: the cheap step
    # that justified it sits 5th now and must still be found.
    fields = []
    for location in range(1, 6):
        field = _field(location, 1, 3)
        field.costs = {lvl: {r: 50_000 for r in vb.RESOURCES} for lvl in field.costs}
        fields.append(field)
    cheap = _field(9, 4, 7)
    cheap.costs = {lvl: {1: 2633, 2: 3162, 3: 2987, 4: 0} for lvl in cheap.costs}
    village = _village(fields + [cheap], resources=5000)
    plan = vb.plan_village(village, 10, 0)
    expensive = [s for s in plan.steps if s.location != 9]
    plan.steps = [expensive[0], expensive[1], expensive[2], expensive[3]] + [
        next(s for s in plan.steps if s.location == 9)]
    plan.afford_hours = 1.4
    assert vb.affordable_alternative(plan).location == 9


def test_towns_build_fields_to_the_town_target():
    village = _village([_field(1, 1, 3)], vid="7")
    assert vb.target_level(village, 14, 10, 12) == 10
    village.is_town = True  # the game says it is a town
    assert vb.target_level(village, 14, 10, 12) == 12
    village.is_town = False
    assert vb.target_level(village, 14, 10, 12, {"7"}) == 12  # marked by the player
    village.is_capital = True
    assert vb.target_level(village, 14, 10, 12, {"7"}) == 14
    plain = _village([_field(1, 1, 3, max_level=12)], vid="8")
    plain.is_town = True
    plans = vb.plan_all([plain], 14, 10, 0, town_target=12)
    assert plans[0].target == 12


def test_completion_forecast_per_village_and_network():
    # Levels 3 and 4 of one field: (400 + 500) × 4 resources, 2 × 10 min.
    slow = _village([_field(1, 1, 3)], resources=0, vid="A")
    fast = _village([_field(1, 1, 3)], resources=0, vid="B",
                    production={1: 1000, 2: 1000, 3: 1000, 4: 1000})
    stocked = _village([_field(1, 1, 3)], resources=100_000, vid="C")
    done = _village([_field(1, 1, 5)], vid="D")
    plans = [vb.VillagePlan(village=v, target=5) for v in (slow, fast, stocked)]
    plans.append(vb.VillagePlan(village=done, target=5, finished=True))
    forecast = vb.completion_forecast(plans)
    # 900 of each resource. Wood grows on the way (the wood field's effect
    # 30 → 50, scaled to the production the game reports: +33/h on
    # average), the others stay at 100/h and decide, resource by resource.
    assert forecast.villages["A"] == pytest.approx(900 / 100)
    assert forecast.limits["A"] == 2  # clay, the first of the slowest
    assert forecast.villages["B"] == pytest.approx(900 / 1000)
    # Everything already in stock: only the build queue is left.
    assert forecast.villages["C"] == pytest.approx(1 / 3)
    assert forecast.queue_bound["C"] and not forecast.queue_bound["A"]
    assert forecast.limits["C"] is None
    assert forecast.villages["D"] == 0.0
    # Shared resources: the network pays for both villages together, the
    # finished village D helps with its 100/h.
    assert forecast.hours == pytest.approx(1800 / 1300)
    assert forecast.network_limit == 2


def test_completion_forecast_does_not_trade_one_resource_for_another():
    # Plenty of crop does not make up for missing clay.
    village = _village([_field(1, 1, 3)], resources=0, vid="A",
                       production={1: 100, 2: 10, 3: 100, 4: 5000})
    forecast = vb.completion_forecast([vb.VillagePlan(village=village, target=5)])
    assert forecast.villages["A"] == pytest.approx(900 / 10)
    assert forecast.limits["A"] == 2 and not forecast.queue_bound["A"]


def test_completion_forecast_counts_the_running_build_and_no_production():
    queued = _village([_field(1, 1, 3)], resources=100_000, vid="A")
    plan = vb.VillagePlan(village=queued, target=5, queue_hours=2.0)
    assert vb.completion_forecast([plan]).villages["A"] == pytest.approx(2 + 1 / 3)
    idle = _village([_field(1, 1, 3, gain_per_level=0)], resources=0, vid="B",
                    production={1: 0, 2: 0, 3: 0, 4: 0})
    forecast = vb.completion_forecast([vb.VillagePlan(village=idle, target=5)])
    assert forecast.villages["B"] is None and forecast.hours is None


def _long_queue_village(vid, production, resources=0):
    fields = []
    for i in range(1, 5):
        f = _field(i, i, 3, cost=1000)
        f.times = {lvl: 6 * 3600 for lvl in f.times}
        fields.append(f)
    return _village(fields, vid=vid, resources=resources, production=production)


def test_gold_budget_does_not_trade_crop_for_missing_clay():
    # Plenty of everything but clay: the queue is not the limit.
    village = _long_queue_village("1", {1: 1e5, 2: 10, 3: 1e5, 4: 1e5})
    plan = vb.plan_village(village, 10, 0)
    allocation = vb.plan_gold_budget([plan], 20, vb.FinishTiers(allow_reduced=True, allow_full=True))
    assert allocation.gold == 0


def test_gold_budget_counts_on_the_network_for_a_poor_village():
    # The capital cannot pay for its fields alone, but a finished village
    # produces plenty: its long queue is worth finishing with gold.
    capital = _long_queue_village("1", {1: 10, 2: 10, 3: 10, 4: 10})
    helper = _village([_field(1, 1, 10)], vid="2", y=1,
                      production={1: 1e5, 2: 1e5, 3: 1e5, 4: 1e5})
    plans = [vb.plan_village(v, 10, 0) for v in (capital, helper)]
    assert plans[1].finished
    allocation = vb.plan_gold_budget(plans, 20, vb.FinishTiers(allow_reduced=True, allow_full=True))
    assert allocation.gold_by_village.get("1", 0) > 0
    forecast = vb.completion_forecast(plans)
    assert forecast.network_limit is None  # the queue, not production
