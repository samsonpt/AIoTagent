from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator


class SupervisorDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    route: Literal["quality_rca", "process_tuner", "maint", "end"]
    reason: str


class RootCauseHypothesis(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    process: Literal["drill", "plating", "etch"]
    confidence: float
    rationale: str
    hypothesis_params: dict = {}


class TuneCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["thickness", "width", "roughness"]
    params: dict
    rationale: str = ""


class TunePlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    candidates: list[TuneCandidate]

    @field_validator("candidates")
    @classmethod
    def max_five_candidates(cls, v: list[TuneCandidate]) -> list[TuneCandidate]:
        if len(v) > 5:
            raise ValueError("candidates must have at most 5 items")
        return v


class MaintPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    intent: str
    target_process: Literal["drill", "plating", "etch"]
    params: dict
    rationale: str
