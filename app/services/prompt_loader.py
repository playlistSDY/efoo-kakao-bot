from functools import lru_cache
from pathlib import Path


PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"


@lru_cache(maxsize=None)
def load_prompt_template(name: str) -> str:
    return (PROMPT_DIR / name).read_text(encoding="utf-8").strip()
