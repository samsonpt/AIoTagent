import dataclasses
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

SOURCES = ("edge", "peer", "cloud", "human", "rule")
HANDLERS = ("edge", "cloud", "human")
ACTION_WEIGHTS = {
    "param_tune": 0.1,
    "dosing": 0.2,
    "bit_change": 0.3,
    "maintenance": 0.3,
    "line_stop": 0.6,
    "lot_hold": 0.7,
    "scrap": 1.0,
}


@dataclass(frozen=True)
class FaultRecord:
    fault_id: str
    process: str
    equipment: str
    fault_type: str
    params: dict
    t_start: float
    t_end: float | None = None
    t_cleared: float | None = None
    cleared_by: str | None = None


@dataclass(frozen=True)
class PanelRecord:
    panel_id: str
    lot_id: str
    part_no: str
    t_release: float
    t_aoi: float
    drill: dict
    plating: dict
    etch: dict
    defects: list[dict]
    root_cause_truth: str
    scrapped: bool


@dataclass(frozen=True)
class ActionRecord:
    t: float
    process: str
    equipment: str
    command: str
    params: dict
    source: str
    category: str
    affected_panels: int
    accepted: bool
    reason: str = ""
    overridden: bool = False
    rolled_back: bool = False
    action_id: int | None = None


@dataclass(frozen=True)
class EpisodeRecord:
    episode_id: str
    process: str
    trigger: str
    t_detect: float
    handler: str
    t_decide: float | None = None
    t_execute: float | None = None
    t_recover: float | None = None
    violated: bool = False
    detail: dict = field(default_factory=dict)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS fault_truth (
    fault_id TEXT PRIMARY KEY, process TEXT, equipment TEXT, fault_type TEXT, params TEXT,
    t_start REAL, t_end REAL, t_cleared REAL, cleared_by TEXT
);
CREATE TABLE IF NOT EXISTS panel_lineage (
    panel_id TEXT PRIMARY KEY, lot_id TEXT, part_no TEXT, t_release REAL, t_aoi REAL,
    drill TEXT, plating TEXT, etch TEXT, defects TEXT, root_cause_truth TEXT, scrapped INTEGER
);
CREATE TABLE IF NOT EXISTS action_log (
    t REAL, process TEXT, equipment TEXT, command TEXT, params TEXT, source TEXT, category TEXT,
    affected_panels INTEGER, accepted INTEGER, reason TEXT, overridden INTEGER, rolled_back INTEGER,
    action_id INTEGER PRIMARY KEY AUTOINCREMENT
);
CREATE TABLE IF NOT EXISTS episode_log (
    episode_id TEXT PRIMARY KEY, process TEXT, trigger TEXT, t_detect REAL, handler TEXT,
    t_decide REAL, t_execute REAL, t_recover REAL, violated INTEGER, detail TEXT
);
CREATE TABLE IF NOT EXISTS telemetry (t REAL, process TEXT, equipment TEXT, key TEXT, value REAL);
"""

_ORDER = {
    "fault_truth": "t_start, fault_id",
    "panel_lineage": "panel_id",
    "action_log": "action_id",
    "episode_log": "t_detect, episode_id",
    "telemetry": "rowid",
}

_JSON_FIELDS = {"params", "drill", "plating", "etch", "defects", "detail"}
_BOOL_FIELDS = {"scrapped", "accepted", "overridden", "rolled_back", "violated"}


def _dumps(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False)


def _encode(rec, exclude: tuple[str, ...] = ()) -> tuple[list[str], list]:
    names, values = [], []
    for f in dataclasses.fields(rec):
        if f.name in exclude:
            continue
        value = getattr(rec, f.name)
        if f.name in _JSON_FIELDS:
            value = _dumps(value)
        elif f.name in _BOOL_FIELDS:
            value = int(value)
        names.append(f.name)
        values.append(value)
    return names, values


def _decode(cls, row: sqlite3.Row):
    kwargs = {}
    for f in dataclasses.fields(cls):
        value = row[f.name]
        if f.name in _JSON_FIELDS:
            value = json.loads(value)
        elif f.name in _BOOL_FIELDS:
            value = bool(value)
        kwargs[f.name] = value
    return cls(**kwargs)


class TraceStore:
    def __init__(self, path: str | Path = ":memory:"):
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def _insert(self, table: str, rec, *, replace: bool = False, exclude: tuple[str, ...] = ()) -> int:
        names, values = _encode(rec, exclude)
        verb = "INSERT OR REPLACE" if replace else "INSERT"
        placeholders = ", ".join("?" * len(names))
        with self._conn:
            cur = self._conn.execute(
                f"{verb} INTO {table} ({', '.join(names)}) VALUES ({placeholders})", values
            )
        return cur.lastrowid

    def _update(self, table: str, key: str, key_value, changes: dict) -> None:
        changes = {k: v for k, v in changes.items() if v is not None}
        if not changes:
            return
        assignments = ", ".join(f"{k} = ?" for k in changes)
        with self._conn:
            self._conn.execute(
                f"UPDATE {table} SET {assignments} WHERE {key} = ?", [*changes.values(), key_value]
            )

    def _select(self, table: str, cls) -> list:
        rows = self._conn.execute(f"SELECT * FROM {table} ORDER BY {_ORDER[table]}")
        return [_decode(cls, row) for row in rows]

    def record_fault(self, rec: FaultRecord) -> None:
        self._insert("fault_truth", rec)

    def update_fault(self, fault_id: str, *, t_end=None, t_cleared=None, cleared_by=None) -> None:
        self._update(
            "fault_truth", "fault_id", fault_id,
            {"t_end": t_end, "t_cleared": t_cleared, "cleared_by": cleared_by},
        )

    def record_panel(self, rec: PanelRecord) -> None:
        self._insert("panel_lineage", rec)

    def record_action(self, rec: ActionRecord) -> int:
        if rec.source not in SOURCES:
            raise ValueError(f"未知动作来源: {rec.source}")
        if rec.category not in ACTION_WEIGHTS:
            raise ValueError(f"未知动作类别: {rec.category}")
        return self._insert("action_log", rec, exclude=("action_id",))

    def mark_action(self, action_id: int, *, overridden: bool | None = None, rolled_back: bool | None = None) -> None:
        changes = {"overridden": overridden, "rolled_back": rolled_back}
        self._update(
            "action_log", "action_id", action_id,
            {k: None if v is None else int(v) for k, v in changes.items()},
        )

    def record_episode(self, rec: EpisodeRecord) -> None:
        if rec.handler not in HANDLERS:
            raise ValueError(f"未知处置方: {rec.handler}")
        self._insert("episode_log", rec, replace=True)

    def record_telemetry(self, t: float, process: str, equipment: str, values: dict[str, float]) -> None:
        with self._conn:
            self._conn.executemany(
                "INSERT INTO telemetry (t, process, equipment, key, value) VALUES (?, ?, ?, ?, ?)",
                [(t, process, equipment, k, values[k]) for k in sorted(values)],
            )

    def faults(self) -> list[FaultRecord]:
        return self._select("fault_truth", FaultRecord)

    def panels(self) -> list[PanelRecord]:
        return self._select("panel_lineage", PanelRecord)

    def actions(self) -> list[ActionRecord]:
        return self._select("action_log", ActionRecord)

    def episodes(self) -> list[EpisodeRecord]:
        return self._select("episode_log", EpisodeRecord)

    def telemetry(self, process: str | None = None, key: str | None = None) -> list[tuple[float, str, str, str, float]]:
        clauses, params = [], []
        if process is not None:
            clauses.append("process = ?")
            params.append(process)
        if key is not None:
            clauses.append("key = ?")
            params.append(key)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"SELECT t, process, equipment, key, value FROM telemetry{where} ORDER BY rowid", params
        )
        return [tuple(row) for row in rows]

    def dump(self) -> dict[str, list[tuple]]:
        return {
            table: [tuple(row) for row in self._conn.execute(f"SELECT * FROM {table} ORDER BY {order}")]
            for table, order in _ORDER.items()
        }

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "TraceStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
