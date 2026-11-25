"""HTML tables as rows and columns (the extra content type).

    {"type": "table", "caption": "Specifications",
     "header": ["Field", "Value"],
     "rows": [["SKU", "TS-00001"], ["Pages", "345"]],
     "row_headers": false}

- **Same width everywhere.** Merged cells (``colspan`` / ``rowspan``) are
  expanded: a merged cell's text is copied into every slot it covers, so
  column 3 of any row always means the same thing as header 3.
- **Column header.** Rows in ``<thead>``, or else the leading rows made only
  of ``<th>`` cells. Several header rows are combined per column
  ("Price / Excl. tax"). ``header`` is ``[]`` when the table has none.
- **Row headers.** ``row_headers`` is true when every body row starts with a
  ``<th>`` label followed by data, like books.toscrape's product table
  (``<th>UPC</th><td>a897…</td>``). The first column is then the label.
- **Layout tables** (``role="presentation"``, a single cell, or a table that
  holds other tables) only position things. They are not data, so the cleaner
  walks into them instead of storing them as a table.
- Columns and rows with no text at all are dropped.

Spans are clamped to the HTML limits, and the width to ``MAX_COLUMNS``, so a
hostile ``colspan="99999999"`` cannot blow up memory.
"""

import html
from dataclasses import dataclass
from typing import Any

from bs4 import Tag

from onramp_scraper.processing.text import collapse

MAX_COLSPAN = 1000  # HTML spec limits
MAX_ROWSPAN = 65534
MAX_COLUMNS = 1000


@dataclass(frozen=True)
class _Cell:
    text: str
    is_header: bool


_EMPTY = _Cell("", is_header=False)


def _own_rows(table: Tag) -> list[Tag]:
    """Rows of this table only, not of tables nested inside its cells."""
    return [tr for tr in table.find_all("tr") if tr.find_parent("table") is table]


def _own_cells(tr: Tag) -> list[Tag]:
    return [cell for cell in tr.find_all(["th", "td"]) if cell.find_parent("tr") is tr]


def _span(cell: Tag, attr: str, maximum: int) -> int:
    """The span attribute as browsers read it: leading digits, clamped; 1 if invalid."""
    raw = str(cell.get(attr) or "").strip()
    digits = raw[: len(raw) - len(raw.lstrip("0123456789"))]
    if not digits:
        return 1
    return min(int(digits), maximum)  # 0 is kept: a rowspan of 0 means "to the end"


def _group_ends(rows: list[Tag]) -> list[int]:
    """For each row, the index of the last row in its group (thead, tbody, tfoot).

    A rowspan never reaches past its own group.
    """
    ends = [0] * len(rows)
    end = len(rows) - 1
    for i in range(len(rows) - 1, -1, -1):
        if i < len(rows) - 1 and rows[i].parent is not rows[i + 1].parent:
            end = i
        ends[i] = end
    return ends


def _grid(rows: list[Tag]) -> list[list[_Cell]]:
    """Expand every span so each row has the same number of cells."""
    ends = _group_ends(rows)
    grid: list[list[_Cell | None]] = [[] for _ in rows]
    for r, tr in enumerate(rows):
        col = 0
        for cell in _own_cells(tr):
            line = grid[r]
            while col < len(line) and line[col] is not None:
                col += 1  # skip slots already filled by a rowspan from above
            colspan = max(_span(cell, "colspan", MAX_COLSPAN), 1)
            rowspan = _span(cell, "rowspan", MAX_ROWSPAN)
            last_row = ends[r] if rowspan == 0 else min(r + rowspan - 1, ends[r])
            value = _Cell(collapse(cell.get_text(" ")), is_header=cell.name == "th")
            for target in grid[r : last_row + 1]:
                for c in range(col, min(col + colspan, MAX_COLUMNS)):
                    target.extend([None] * (c + 1 - len(target)))
                    if target[c] is None:  # overlapping cells: the first one wins
                        target[c] = value
            col += colspan
    width = max((len(line) for line in grid), default=0)
    return [[c or _EMPTY for c in line] + [_EMPTY] * (width - len(line)) for line in grid]


def is_layout_table(table: Tag) -> bool:
    """True when the table only positions content and holds no tabular data."""
    if str(table.get("role", "")).lower() in ("presentation", "none"):
        return True
    if any(inner.find_parent("table") is table for inner in table.find_all("table")):
        return True
    return sum(len(_own_cells(tr)) for tr in _own_rows(table)) == 1


def _is_header_row(row: list[_Cell]) -> bool:
    """Only <th> cells, ignoring an empty corner cell."""
    filled = [cell for cell in row if cell.text]
    return bool(filled) and all(cell.is_header for cell in filled)


def _header_row_count(rows: list[Tag], grid: list[list[_Cell]]) -> int:
    in_thead = 0
    while in_thead < len(rows) and rows[in_thead].find_parent("thead") is not None:
        in_thead += 1
    if in_thead:
        return in_thead
    count = 0
    while count < len(grid) and _is_header_row(grid[count]):
        count += 1
    return min(count, 1) if count == len(grid) else count  # an all-<th> table: first row only


def _combine(column: list[str]) -> str:
    """One header text from several header rows, without repeating a merged cell."""
    parts: list[str] = []
    for text in column:
        if text and text not in parts:
            parts.append(text)
    return " / ".join(parts)


def parse_table(table: Tag) -> dict[str, Any] | None:
    """The table as one block, or None when it holds no text."""
    rows = _own_rows(table)
    grid = _grid(rows)
    if not grid or not grid[0]:
        return None

    split = _header_row_count(rows, grid)
    header_rows, body = grid[:split], grid[split:]
    body = [row for row in body if any(cell.text for cell in row)]
    row_headers = bool(body) and all(
        row[0].is_header and any(not cell.is_header for cell in row[1:]) for row in body
    )

    header = [_combine([row[c].text for row in header_rows]) for c in range(len(grid[0]))]
    texts = [[cell.text for cell in row] for row in body]
    keep = [c for c, title in enumerate(header) if title or any(row[c] for row in texts)]
    if not keep:
        return None
    header = [header[c] for c in keep] if any(header) else []
    texts = [[row[c] for c in keep] for row in texts]

    caption_tag = table.find("caption", recursive=False)
    caption = collapse(caption_tag.get_text(" ")) if caption_tag else ""
    return {
        "type": "table",
        "caption": caption or None,
        "header": header,
        "rows": texts,
        "row_headers": row_headers,
    }


def table_to_html(block: dict[str, Any]) -> str:
    """Render a table block back to plain HTML (no spans; merged cells stay copied)."""
    out = ["<table>"]
    if block.get("caption"):
        out.append(f"<caption>{html.escape(block['caption'])}</caption>")
    if block["header"]:
        cells = "".join(f"<th>{html.escape(text)}</th>" for text in block["header"])
        out.append(f"<thead><tr>{cells}</tr></thead>")
    out.append("<tbody>")
    for row in block["rows"]:
        cells = "".join(
            f"<th>{html.escape(text)}</th>"
            if c == 0 and block.get("row_headers")
            else f"<td>{html.escape(text)}</td>"
            for c, text in enumerate(row)
        )
        out.append(f"<tr>{cells}</tr>")
    out.append("</tbody></table>")
    return "".join(out)
