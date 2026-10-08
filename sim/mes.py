from dataclasses import dataclass

from bench.schema import PanelRecord, TraceStore
from common.recipe import Recipe
from sim.aoi import PanelInspection

SCRAP_DEFECT = {"type": "scrapped_by_command", "zone": [0, 0], "stage": "line"}


@dataclass(frozen=True)
class Lot:
    lot_id: str
    panel_ids: list[str]
    t_release: float


class Mes:
    def __init__(self, recipe: Recipe):
        self._recipe = recipe
        self._count = 0
        self._steps: dict[str, dict[str, dict]] = {}
        self._held: set[str] = set()

    def release_lot(self, t: float) -> Lot:
        self._count += 1
        lot_id = f"L{self._count:04d}"
        self._steps[lot_id] = {}
        panel_ids = [f"{lot_id}-P{k:02d}" for k in range(1, self._recipe.lot_size + 1)]
        return Lot(lot_id, panel_ids, t)

    def _check(self, lot_id: str) -> dict[str, dict]:
        if lot_id not in self._steps:
            raise KeyError(lot_id)
        return self._steps[lot_id]

    def record_step(self, lot_id: str, process: str, params: dict) -> None:
        self._check(lot_id)[process] = dict(params)

    def hold(self, lot_id: str) -> None:
        self._check(lot_id)
        self._held.add(lot_id)

    def is_held(self, lot_id: str) -> bool:
        return lot_id in self._held

    def _record(self, lot: Lot, panel_id: str, t_aoi: float, defects: list[dict], root_cause: str,
                scrapped: bool, store: TraceStore) -> None:
        steps = self._check(lot.lot_id)
        store.record_panel(
            PanelRecord(
                panel_id=panel_id,
                lot_id=lot.lot_id,
                part_no=self._recipe.part_no,
                t_release=lot.t_release,
                t_aoi=t_aoi,
                drill=steps.get("drill", {}),
                plating=steps.get("plating", {}),
                etch=steps.get("etch", {}),
                defects=defects,
                root_cause_truth=root_cause,
                scrapped=scrapped,
            )
        )

    def finish(self, lot: Lot, t_aoi: float, inspections: list[PanelInspection], store: TraceStore) -> None:
        if [i.panel_id for i in inspections] != lot.panel_ids:
            raise ValueError(f"{lot.lot_id} 检测结果与拼板不一致")
        for insp in inspections:
            defects = [{"type": d.type, "zone": list(d.zone), "stage": d.stage} for d in insp.defects]
            self._record(lot, insp.panel_id, t_aoi, defects, insp.root_cause_truth, insp.scrapped, store)

    def scrap(self, lot: Lot, t: float, store: TraceStore) -> None:
        for panel_id in lot.panel_ids:
            self._record(lot, panel_id, t, [dict(SCRAP_DEFECT)], "none", True, store)
