from pathlib import Path

import numpy as np
import pytest

from common.recipe import load_recipe
from common.rng import make_rng
from sim.aoi import Defect, PanelInspection, inspect_lot
from sim.drill import DrillResult
from sim.etch import EtchResult
from sim.faults import FaultSpec
from sim.plating import PlatingResult

ROOT = Path(__file__).resolve().parents[2]
RECIPE = load_recipe(ROOT / "bench" / "recipes" / "PN-4L-001.yaml")


class NoBackground:
    def random(self):
        return 0.5


def results(n=1, *, width=100.0, overetch=5.0, thickness=25.0, roughness=15.0, broken=False):
    shape = (n, 3, 3)
    drill = DrillResult("L0001", roughness, broken, {})
    plating = PlatingResult("L0001", np.full(shape, thickness), {})
    etch = EtchResult("L0001", np.full(shape, width), np.full(shape, overetch), {})
    return drill, plating, etch


def ids(n=1):
    return [f"L0001-P{k:02d}" for k in range(1, n + 1)]


def inspect_one(drill, plating, etch, active=None):
    [insp] = inspect_lot(ids(), drill, plating, etch, RECIPE, NoBackground(), active or {})
    return insp


def fault(fault_id, type_, process):
    return FaultSpec(fault_id=fault_id, type=type_, process=process, start_tick=0)


def test_nominal_panel_is_clean():
    insp = inspect_one(*results())
    assert insp == PanelInspection(panel_id="L0001-P01", defects=[], root_cause_truth="none", scrapped=False)


@pytest.mark.parametrize(
    "kwargs,expected,scrapped",
    [
        ({"width": 84.9}, "open", True),
        ({"width": 85.0}, "width_under", False),
        ({"width": 89.9}, "width_under", False),
        ({"width": 90.0}, None, False),
        ({"width": 110.0}, None, False),
        ({"width": 110.1}, "width_over", False),
        ({"overetch": -3.1}, "short", True),
        ({"overetch": -3.0}, "residue", False),
        ({"overetch": -1.1}, "residue", False),
        ({"overetch": -1.0}, None, False),
        ({"thickness": 19.9}, "thin_copper", False),
        ({"thickness": 20.0}, None, False),
    ],
)
def test_zone_thresholds(kwargs, expected, scrapped):
    insp = inspect_one(*results(**kwargs))
    types = {d.type for d in insp.defects}
    if expected is None:
        assert insp.defects == []
    else:
        assert types == {expected}
        assert len(insp.defects) == 9
        assert {d.zone for d in insp.defects} == {(i, j) for i in range(3) for j in range(3)}
    assert insp.scrapped is scrapped


def test_defect_stage_and_single_zone():
    drill, plating, etch = results()
    plating.thickness_um[0, 1, 2] = 18.0
    etch.overetch_um[0, 2, 0] = -2.0
    insp = inspect_one(drill, plating, etch)
    assert insp.defects == [Defect("thin_copper", (1, 2), "plating"), Defect("residue", (2, 0), "etch")]


def test_hole_defects():
    insp = inspect_one(*results(roughness=25.1))
    assert insp.defects == [Defect("hole_wall", (0, 0), "drill")]
    assert not insp.scrapped
    assert inspect_one(*results(roughness=25.0)).defects == []
    insp = inspect_one(*results(roughness=30.0, broken=True))
    assert insp.defects == [Defect("hole_missing", (0, 0), "drill")]
    assert insp.scrapped


def test_drill_result_applies_to_every_panel():
    out = inspect_lot(ids(3), *results(3, broken=True), RECIPE, NoBackground(), {})
    assert [i.panel_id for i in out] == ids(3)
    assert all(i.scrapped for i in out)


def test_root_cause_prefers_defect_stage_then_etch_plating_drill():
    drill, plating, etch = results()
    etch.width_um[0, 0, 0] = 87.0
    active = {"plating": [fault("P1", "rectifier_low", "plating")], "etch": [fault("E1", "nozzle_clog", "etch")]}
    assert inspect_one(drill, plating, etch, active).root_cause_truth == "etch"
    assert inspect_one(drill, plating, etch, {"plating": active["plating"]}).root_cause_truth == "plating"
    assert inspect_one(drill, plating, etch, {"drill": [fault("D1", "drill_break", "drill")]}).root_cause_truth == "none"


def test_root_cause_ordering_for_plating_defect():
    drill, plating, etch = results(thickness=19.0)
    active = {"plating": [fault("P1", "additive_depletion", "plating")], "etch": [fault("E1", "etch_sg_drift", "etch")]}
    assert inspect_one(drill, plating, etch, active).root_cause_truth == "plating"
    assert inspect_one(drill, plating, etch, {"etch": active["etch"]}).root_cause_truth == "none"


def test_root_cause_ignores_sensor_faults_and_uses_first_attributed_defect():
    drill, plating, etch = results(roughness=26.0)
    etch.width_um[0, 0, 0] = 87.0
    active = {"etch": [fault("S1", "sensor_bias", "etch")], "drill": [fault("D1", "drill_abnormal_wear", "drill")]}
    insp = inspect_one(drill, plating, etch, active)
    assert [d.type for d in insp.defects] == ["width_under", "hole_wall"]
    assert insp.root_cause_truth == "drill"


def test_defect_cause_attributed_per_defect():
    drill, plating, etch = results(roughness=26.0)
    etch.width_um[0, 0, 0] = 87.0
    plating.thickness_um[0, 1, 1] = 18.0
    active = {"plating": [fault("P1", "rectifier_low", "plating")]}
    insp = inspect_one(drill, plating, etch, active)
    assert [(d.type, d.cause) for d in insp.defects] == [
        ("width_under", "plating"),
        ("thin_copper", "plating"),
        ("hole_wall", "none"),
    ]
    assert Defect("open", (0, 0), "etch").cause == "none"


def test_background_defects_reproducible_by_seed():
    n = 2000
    args = (ids(n), *results(n))
    a = inspect_lot(*args, RECIPE, make_rng(5, "aoi"), {})
    b = inspect_lot(*args, RECIPE, make_rng(5, "aoi"), {})
    c = inspect_lot(*args, RECIPE, make_rng(6, "aoi"), {})
    assert a == b
    assert a != c
    hit = [i for i in a if i.defects]
    assert 5 <= len(hit) <= 40
    for insp in hit:
        [d] = insp.defects
        assert d.type in {"residue", "width_under"}
        assert d.stage == "etch"
        assert all(type(z) is int and 0 <= z < 3 for z in d.zone)
        assert insp.root_cause_truth == "none"
        assert not insp.scrapped
    assert {i.defects[0].type for i in hit} == {"residue", "width_under"}
