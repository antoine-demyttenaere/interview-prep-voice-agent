"""Loads the prompt texts (prompts/*.md) and scoring rubrics (rubrics/*.yaml) for the nodes.

Prompts live in files, not in code, so you can improve them without touching Python, and git
keeps their history (each commit is a prompt version). Files are re-read on every call: save a
prompt and the next question already uses it, without restarting anything.

- render("ask", transcript=..., directive=...) fills the $placeholders of prompts/ask.md.
  A missing value raises an error, so a typo in a placeholder fails loudly.
- mode_guidance("behavioral") returns prompts/modes/behavioral.md (the interview-type text).
- rubric_text("behavioral") turns rubrics/behavioral.yaml into text for a prompt.
The <!-- ... --> header at the top of each prompt file is removed before it is sent to Claude.
"""

import re
from pathlib import Path
from string import Template

import yaml

from app.voice import LANGUAGES

ROOT = Path(__file__).resolve().parents[2]
PROMPTS = ROOT / "prompts"
RUBRICS = ROOT / "rubrics"

_HEADER = re.compile(r"\A\s*<!--.*?-->\s*", re.DOTALL)


def _read(path: Path) -> str:
    return _HEADER.sub("", path.read_text(encoding="utf-8")).strip()


def language_name(code: str) -> str:
    """'fr' -> 'French', for prompts. Unknown codes fall back to English."""
    return LANGUAGES.get(code, "English")


def mode_guidance(mode: str) -> str:
    path = PROMPTS / "modes" / f"{mode}.md"
    if not path.exists():
        raise ValueError(f"Unknown mode '{mode}': no {path.relative_to(ROOT)}")
    return _read(path)


def render(name: str, **values: object) -> str:
    """Fill prompts/<name>.md. Missing placeholders raise, so typos fail loudly."""
    return Template(_read(PROMPTS / f"{name}.md")).substitute(**values)


def load_rubric(mode: str) -> dict:
    path = RUBRICS / f"{mode}.yaml"
    if not path.exists():
        raise ValueError(f"No rubric for mode '{mode}': {path.relative_to(ROOT)}")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def rubric_text(mode: str) -> str:
    """The rubric as plain text for a prompt."""
    rubric = load_rubric(mode)
    lines = ["Scale: " + "; ".join(f"{k} = {v}" for k, v in rubric["scale"].items()), "Criteria:"]
    for c in rubric["criteria"]:
        lines.append(f"- {c['id']} ({c['name']}): {c['description']}")
    return "\n".join(lines)
