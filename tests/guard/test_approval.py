from pathlib import Path

from bench.human_model import HumanModel
from bench.schema import FaultRecord, TraceStore
from common import topics
from common.bus import InMemoryBus
from common.clock import SimClock
from common.config import AblationConfig
from common.recipe import load_recipe
from common.rng import make_rng
from guard.action_guard import ActionGuard

ROOT = Path(__file__).resolve().parents[2]
RECIPE = load_recipe(ROOT / "bench" / "recipes" / "PN-4L-001.yaml")


def _setup(*, seed: int = 42, with_fault: bool = True):
    bus, store, clock = InMemoryBus(), TraceStore(), SimClock()
    ablation = AblationConfig(use_human_gate=True, use_twin_lookahead=False)
    guard = ActionGuard(bus, store, RECIPE, clock, None, seed=seed, ablation=ablation)
    human = HumanModel(
        bus,
        RECIPE,
        clock,
        store=store,
        guard=guard,
        seed=seed,
        ocap=False,
        approval_delay_s=900,
    )
    if with_fault:
        store.record_fault(
            FaultRecord(
                fault_id="f1",
                process="line",
                equipment="line",
                fault_type="hold",
                params={},
                t_start=0.0,
            )
        )
    return bus, store, clock, guard, human


def test_approval_delay_and_seed_stable():
    _, store, clock, _, human = _setup(seed=42, with_fault=True)
    rid = store.enqueue_approval(
        t_submit=0.0,
        process="line",
        equipment="line",
        command="hold_lot",
        params={"lot_id": "L1"},
        source="edge",
        lot_id="L1",
        topic=topics.line_command(),
    )
    human.on_tick(clock)
    assert store.list_approvals("pending")[0]["request_id"] == rid

    clock.tick = 0
    # now = tick * 1800; need now >= 900 → tick >= 1 is enough if we also set substep,
    # but t_submit + 900 = 900; at tick=0 now=0. Advance to tick where now >= 900.
    while clock.now < 900:
        clock.advance_tick()
    human.on_tick(clock)
    decided = [r for r in store.list_approvals() if r["request_id"] == rid][0]
    assert decided["status"] in ("approved", "rejected")
    assert decided["decider"] == "human_model"
    assert decided["t_decide"] == clock.now

    # Same seed → same first RNG draw → same decision
    expected_approve = make_rng(42, "human_approval").random() < 0.9
    assert (decided["status"] == "approved") is expected_approve

    statuses = []
    for _ in range(2):
        bus2, store2, clock2, _, human2 = _setup(seed=42, with_fault=True)
        store2.enqueue_approval(
            t_submit=0.0,
            process="line",
            equipment="line",
            command="hold_lot",
            params={"lot_id": "L1"},
            source="edge",
            lot_id="L1",
            topic=topics.line_command(),
        )
        while clock2.now < 900:
            clock2.advance_tick()
        human2.on_tick(clock2)
        statuses.append(store2.list_approvals()[0]["status"])
    assert statuses[0] == statuses[1]


def test_approval_reject_when_process_mismatches_fault():
    _, store, clock, _, human = _setup(seed=42, with_fault=False)
    store.record_fault(
        FaultRecord(
            fault_id="f1",
            process="etch",
            equipment="ETC-01",
            fault_type="sg_drift",
            params={},
            t_start=0.0,
        )
    )
    store.enqueue_approval(
        t_submit=0.0,
        process="line",
        equipment="line",
        command="hold_lot",
        params={"lot_id": "L2"},
        source="edge",
        lot_id="L2",
        topic=topics.line_command(),
    )
    while clock.now < 900:
        clock.advance_tick()
    human.on_tick(clock)
    # first draw 0.149 < 0.9 → reject when process does not match
    assert store.list_approvals()[0]["status"] == "rejected"
    assert any(a.command == "hold_lot" and a.accepted is False for a in store.actions())
