from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict


class AblationConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = "full"
    use_edge_agent: bool = True
    use_cloud: bool = True
    use_twin_lookahead: bool = True
    use_peer_feedforward: bool = True
    use_event_trigger: bool = True
    use_confidence_modulation: bool = True
    use_rag: bool = True
    use_human_gate: bool = True
    twin_fidelity: Literal["none", "mechanistic", "hybrid"] = "hybrid"
    use_counterfactual_rca: bool = True
    use_twin_confidence_gate: bool = True


def load_ablation(path: str | Path) -> AblationConfig:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return AblationConfig.model_validate(data)
