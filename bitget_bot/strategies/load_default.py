"""
Loads built-in strategies for Strategy Studio.
Exposed via GET /api/strategy/builtin/{name}
"""
from pathlib import Path

_ROOT = Path(__file__).parent

BUILTIN_STRATEGIES = {
    "ma_squeeze": {
        "name": "均线密集 + 量价确认（内置）",
        "markdown": (_ROOT / "ma_squeeze_studio.md").read_text(encoding="utf-8"),
        "code": (_ROOT / "ma_squeeze_studio.py").read_text(encoding="utf-8"),
    }
}
