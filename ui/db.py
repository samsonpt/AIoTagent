import json
from pathlib import Path

from bench.schema import TraceStore

_CHAIN_COLS = ("seq", "lot_id", "kind", "ref", "t", "payload", "prev_hash", "entry_hash")


class DashboardStore:
    def __init__(self, path: str | Path):
        self._store = TraceStore(path)

    def verify_chain(self, lot_id: str | None = None) -> tuple[bool, str]:
        return self._store.verify_chain(lot_id)

    def list_approvals(self, status: str | None = None) -> list[dict]:
        return self._store.list_approvals(status)

    def decide_approval(
        self,
        request_id: str,
        approved: bool,
        *,
        t_decide: float,
        reason: str = "",
    ) -> None:
        status = "approved" if approved else "rejected"
        self._store.update_approval(
            request_id,
            status=status,
            t_decide=t_decide,
            decider="ui",
            reason=reason,
        )

    def telemetry(self, process: str | None = None, limit: int = 500) -> list[tuple]:
        rows = self._store.telemetry(process=process)
        if limit is not None and len(rows) > limit:
            return rows[-limit:]
        return rows

    def chain_rows(self, lot_id: str) -> list[dict]:
        result = []
        for row in self._store.dump().get("trace_chain", ()):
            if row[1] != lot_id:
                continue
            item = dict(zip(_CHAIN_COLS, row))
            item["payload"] = json.loads(item["payload"])
            result.append(item)
        return result

    def lot_ids(self) -> list[str]:
        rows = self._store.dump().get("trace_chain", ())
        return sorted({row[1] for row in rows})

    def panels(self) -> list:
        return self._store.panels()

    def episodes(self) -> list:
        return self._store.episodes()

    def close(self) -> None:
        self._store.close()

    def __enter__(self) -> "DashboardStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
