from typing import Literal

from pydantic import BaseModel, ConfigDict

ProcessName = Literal["drill", "plating", "etch"]
SourceName = Literal["edge", "peer", "cloud", "human"]


class Intent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    intent: str
    target_process: ProcessName
    params: dict = {}
    lot_id: str | None = None
    recipe_window: dict[str, float] | None = None
    deadline: float | None = None
    rationale: str = ""
    policy_version: str = "m2"
    source: SourceName = "edge"
