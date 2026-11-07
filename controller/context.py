"""Everything a planner needs about one workflow at one simulated minute, read from our store only."""
from __future__ import annotations

from dataclasses import dataclass, field

from .cohorts import Layout, Registry
from .store import Store


@dataclass
class WfCtx:
    store: Store
    reg: Registry
    wf: str
    spec: dict                 # GET /workflows/{id} as captured
    cfg: dict                  # live config (our snapshot of the current version)
    version: int
    now: int                   # sim minute
    combos: dict[int, dict]    # feat code -> {feature: value}
    layout: Layout = field(init=False)

    def __post_init__(self):
        self.layout = Layout.from_config(self.cfg, self.reg)

    @property
    def gov(self) -> dict:
        return self.spec["governance"]

    @property
    def auto(self) -> dict:
        return self.gov.get("auto_apply") or {}

    def auto_allows(self, dim: str) -> bool:
        return bool(self.auto.get("enabled")) and dim in (self.auto.get("allowed_dims") or [])

    @property
    def options(self) -> dict:
        return self.spec["options"]

    @property
    def primary(self) -> str:
        return self.spec["objective"]["primary"]

    @property
    def day(self) -> int:
        return self.now // 1440

    @property
    def hour(self) -> int:
        return (self.now % 1440) // 60

    def supervisor_touched(self, dim: str, hours: int) -> bool:
        rows = self.store.q("SELECT dims, applied_minute FROM config_versions WHERE wf=? AND actor='supervisor' "
                            "AND applied_minute IS NOT NULL AND applied_minute > ?", (self.wf, self.now - hours * 60))
        return any(dim in (d or "") for d, _ in rows)
