from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, model_validator


class ParamWindow(BaseModel):
    model_config = ConfigDict(frozen=True)

    min: float
    max: float
    target: float

    @model_validator(mode="after")
    def _check_order(self) -> "ParamWindow":
        if not self.min <= self.target <= self.max:
            raise ValueError(f"target {self.target} outside [{self.min}, {self.max}]")
        return self

    def contains(self, v: float) -> bool:
        return self.min <= v <= self.max

    def clamp(self, v: float) -> float:
        return min(max(v, self.min), self.max)


class SpecLimit(BaseModel):
    model_config = ConfigDict(frozen=True)

    min: float | None = None
    max: float | None = None
    target: float

    def contains(self, v: float) -> bool:
        if self.min is not None and v < self.min:
            return False
        if self.max is not None and v > self.max:
            return False
        return True


class Recipe(BaseModel):
    model_config = ConfigDict(frozen=True)

    part_no: str
    lot_size: int
    windows: dict[str, dict[str, ParamWindow]]
    specs: dict[str, SpecLimit]
    constants: dict[str, float]

    def window(self, process: str, param: str) -> ParamWindow:
        return self.windows[process][param]

    def violations(self, process: str, params: dict[str, float]) -> list[str]:
        defined = self.windows.get(process, {})
        out = []
        for param, value in params.items():
            w = defined.get(param)
            if w is not None and not w.contains(value):
                out.append(f"{process}.{param}={float(value)} outside [{w.min}, {w.max}]")
        return out


def load_recipe(path: str | Path) -> Recipe:
    return Recipe.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
