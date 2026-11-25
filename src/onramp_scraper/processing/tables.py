"""HTML tables as rows and columns (the extra content type).

    {"type": "table", "caption": "...", "header": ["Field", "Value"],
     "rows": [["SKU", "TS-00001"], ["Pages", "345"]]}

Task D3 extends this (merged cells, header detection); D2 needs the basic shape.
"""

import re
from typing import Any

from bs4 import Tag

_SPACE = re.compile(r"\s+")


def _cell_text(cell: Tag) -> str:
    return _SPACE.sub(" ", cell.get_text(" ")).strip()


def _rows(table: Tag) -> list[Tag]:
    """Rows of this table only, not of tables nested inside its cells."""
    return [tr for tr in table.find_all("tr") if tr.find_parent("table") is table]


def parse_table(table: Tag) -> dict[str, Any] | None:
    rows = [
        [cell for cell in tr.find_all(["th", "td"]) if cell.find_parent("tr") is tr]
        for tr in _rows(table)
    ]
    rows = [r for r in rows if r]
    if not rows:
        return None

    header: list[str] = []
    first = rows[0]
    in_thead = first[0].find_parent("thead") is not None
    if in_thead or all(cell.name == "th" for cell in first):
        header = [_cell_text(cell) for cell in first]
        rows = rows[1:]
    body = [[_cell_text(cell) for cell in row] for row in rows]
    body = [row for row in body if any(row)]
    if not header and not body:
        return None

    caption_tag = table.find("caption")
    caption = _SPACE.sub(" ", caption_tag.get_text(" ")).strip() if caption_tag else None
    return {"type": "table", "caption": caption or None, "header": header, "rows": body}
