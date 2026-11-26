"""Whitespace and punctuation tidying shared by every block type."""

import re

_SPACE = re.compile(r"\s+")
# Inline elements are joined with spaces so words never run together; this
# removes the stray space that leaves before punctuation ("Austen , Brontë").
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.;:!?%)\]])")
_SPACE_AFTER_OPEN = re.compile(r"([(\[])\s+")


def collapse(text: str) -> str:
    text = _SPACE.sub(" ", text).strip()
    return _SPACE_AFTER_OPEN.sub(r"\1", _SPACE_BEFORE_PUNCT.sub(r"\1", text))
