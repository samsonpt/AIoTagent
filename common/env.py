from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_env(*, root: Path | None = None) -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(root or ROOT / ".env", override=False)
