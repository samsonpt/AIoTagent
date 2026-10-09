"""消融矩阵 CLI。报告汇总留到后续任务，本脚本只写 results.csv 与 manifest.json。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bench.matrix_runner import main

if __name__ == "__main__":
    raise SystemExit(main())
