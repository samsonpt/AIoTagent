from pathlib import Path

import pytest

from bench.schema import PanelRecord, TraceStore
from common.recipe import load_recipe
from sim.aoi import Defect, PanelInspection
from sim.mes import Lot, Mes

ROOT = Path(__file__).resolve().parents[2]
RECIPE = load_recipe(ROOT / "bench" / "recipes" / "PN-4L-001.yaml")


def test_release_lot_numbering():
    mes = Mes(RECIPE)
    a = mes.release_lot(0.0)
    b = mes.release_lot(1800.0)
    assert a == Lot("L0001", [f"L0001-P{k:02d}" for k in range(1, 13)], 0.0)
    assert (b.lot_id, b.panel_ids[0], b.panel_ids[-1], b.t_release) == ("L0002", "L0002-P01", "L0002-P12", 1800.0)


def test_hold():
    mes = Mes(RECIPE)
    lot = mes.release_lot(0.0)
    assert not mes.is_held(lot.lot_id)
    mes.hold(lot.lot_id)
    assert mes.is_held(lot.lot_id)
    with pytest.raises(KeyError):
        mes.hold("L0099")


def test_record_step_rejects_unknown_lot():
    with pytest.raises(KeyError):
        Mes(RECIPE).record_step("L0099", "drill", {})


def test_finish_writes_panel_lineage():
    mes = Mes(RECIPE)
    lot = mes.release_lot(100.0)
    mes.record_step(lot.lot_id, "drill", {"spindle_rpm": 120000.0, "bit_hits": 400})
    mes.record_step(lot.lot_id, "plating", {"current_density_asd": 2.0})
    mes.record_step(lot.lot_id, "etch", {"sg": 1.28})
    inspections = [
        PanelInspection(pid, [], "none", False) for pid in lot.panel_ids
    ]
    inspections[1] = PanelInspection(
        lot.panel_ids[1], [Defect("open", (2, 1), "etch"), Defect("hole_wall", (0, 0), "drill")], "etch", True
    )
    store = TraceStore()
    mes.finish(lot, 9000.0, inspections, store)
    panels = store.panels()
    assert [p.panel_id for p in panels] == lot.panel_ids
    assert panels[1] == PanelRecord(
        panel_id="L0001-P02",
        lot_id="L0001",
        part_no=RECIPE.part_no,
        t_release=100.0,
        t_aoi=9000.0,
        drill={"spindle_rpm": 120000.0, "bit_hits": 400},
        plating={"current_density_asd": 2.0},
        etch={"sg": 1.28},
        defects=[
            {"type": "open", "zone": [2, 1], "stage": "etch"},
            {"type": "hole_wall", "zone": [0, 0], "stage": "drill"},
        ],
        root_cause_truth="etch",
        scrapped=True,
    )
    assert panels[0].defects == [] and not panels[0].scrapped


def test_finish_rejects_mismatched_inspections():
    mes = Mes(RECIPE)
    lot = mes.release_lot(0.0)
    with pytest.raises(ValueError):
        mes.finish(lot, 1.0, [PanelInspection("L0001-P01", [], "none", False)], TraceStore())


def test_scrap_writes_scrapped_records():
    mes = Mes(RECIPE)
    lot = mes.release_lot(0.0)
    mes.record_step(lot.lot_id, "drill", {"spindle_rpm": 120000.0})
    store = TraceStore()
    mes.scrap(lot, 3600.0, store)
    panels = store.panels()
    assert len(panels) == 12
    for p in panels:
        assert p.defects == [{"type": "scrapped_by_command", "zone": [0, 0], "stage": "line"}]
        assert p.root_cause_truth == "none"
        assert p.scrapped
        assert p.t_aoi == 3600.0
        assert p.drill == {"spindle_rpm": 120000.0}
        assert p.plating == {} and p.etch == {}
