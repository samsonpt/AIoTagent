"""消融矩阵 CLI。跑完后由 matrix_runner 调用 write_reports 写出汇总与 report.md。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bench.matrix_runner import main

if __name__ == "__main__":
    raise SystemExit(main())
