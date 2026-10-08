from dataclasses import dataclass, replace

import numpy as np

from common.recipe import Recipe
from sim.drill import DrillResult
from sim.etch import EtchResult
from sim.faults import FAULT_DEFECT_LINKS, FaultSpec
from sim.plating import PlatingResult

BACKGROUND_DEFECT_PROB = 0.01
BACKGROUND_TYPES = ("residue", "width_under")
SCRAP_TYPES = {"open", "short", "hole_missing"}
STAGE_PRIORITY = ("etch", "plating", "drill")


def defect_stage(defect_type: str) -> str:
    if defect_type.startswith("hole_"):
        return "drill"
    if defect_type == "thin_copper":
        return "plating"
    return "etch"


@dataclass(frozen=True)
class Defect:
    type: str
    zone: tuple[int, int]
    stage: str
    cause: str = "none"


@dataclass(frozen=True)
class PanelInspection:
    panel_id: str
    defects: list[Defect]
    root_cause_truth: str
    scrapped: bool


def _defect(defect_type: str, i: int, j: int) -> Defect:
    return Defect(defect_type, (int(i), int(j)), defect_stage(defect_type))


def _zone_defects(width: float, overetch: float, thickness: float) -> list[str]:
    out = []
    if width < 85:
        out.append("open")
    elif width < 90:
        out.append("width_under")
    elif width > 110:
        out.append("width_over")
    if overetch < -3:
        out.append("short")
    elif overetch < -1:
        out.append("residue")
    if thickness < 20:
        out.append("thin_copper")
    return out


def _root_cause(defect: Defect, active_faults: dict[str, list[FaultSpec]]) -> str:
    order = (defect.stage, *(p for p in STAGE_PRIORITY if p != defect.stage))
    for process in order:
        for spec in active_faults.get(process, []):
            if defect.type in FAULT_DEFECT_LINKS[spec.type]:
                return spec.process
    return "none"


def inspect_lot(
    panel_ids: list[str],
    drill: DrillResult,
    plating: PlatingResult,
    etch: EtchResult,
    recipe: Recipe,
    rng: np.random.Generator,
    active_faults: dict[str, list[FaultSpec]],
) -> list[PanelInspection]:
    del recipe
    out = []
    for k, panel_id in enumerate(panel_ids):
        defects = []
        for i in range(3):
            for j in range(3):
                for t in _zone_defects(etch.width_um[k, i, j], etch.overetch_um[k, i, j], plating.thickness_um[k, i, j]):
                    defects.append(_defect(t, i, j))
        if drill.broken:
            defects.append(_defect("hole_missing", 0, 0))
        elif drill.roughness_um > 25:
            defects.append(_defect("hole_wall", 0, 0))
        defects = [replace(d, cause=_root_cause(d, active_faults)) for d in defects]
        if rng.random() < BACKGROUND_DEFECT_PROB:
            t = BACKGROUND_TYPES[rng.integers(len(BACKGROUND_TYPES))]
            defects.append(_defect(t, rng.integers(3), rng.integers(3)))
        out.append(
            PanelInspection(
                panel_id=panel_id,
                defects=defects,
                root_cause_truth=next((d.cause for d in defects if d.cause != "none"), "none"),
                scrapped=any(d.type in SCRAP_TYPES for d in defects),
            )
        )
    return out
