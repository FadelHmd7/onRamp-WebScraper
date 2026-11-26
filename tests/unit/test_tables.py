"""Tables as rows and columns: merged cells, headers, layout tables, round trips."""

import random
from pathlib import Path
from typing import Any

import pytest
from bs4 import BeautifulSoup, Tag

from onramp_scraper.processing.clean import clean_html
from onramp_scraper.processing.tables import (
    MAX_COLUMNS,
    is_layout_table,
    parse_table,
    table_to_html,
)
from onramp_testsite import content

FIXTURES = Path(__file__).parents[1] / "fixtures"


def first_table(markup: str) -> Tag:
    table = BeautifulSoup(markup, "lxml").find("table")
    assert isinstance(table, Tag)
    return table


def parse(markup: str) -> dict[str, Any] | None:
    return parse_table(first_table(markup))


def table_blocks(markup: str) -> list[dict[str, Any]]:
    return [b for b in clean_html(markup).blocks if b["type"] == "table"]


def round_trip(block: dict[str, Any]) -> dict[str, Any] | None:
    return parse(table_to_html(block))


# ---------------------------------------------------------------- real pages


def test_books_product_table_has_row_headers() -> None:
    html = (FIXTURES / "books-toscrape" / "book-a-light-in-the-attic.html").read_text("utf-8")
    [table] = table_blocks(html)
    assert table["header"] == []
    assert table["row_headers"] is True
    assert table["rows"][0] == ["UPC", "a897fe39b1053632"]
    assert ["Availability", "In stock (22 available)"] in table["rows"]
    assert ["Number of reviews", "0"] in table["rows"]
    assert all(len(row) == 2 for row in table["rows"])


def test_testsite_specifications_table() -> None:
    item = content.item(3)
    [table] = table_blocks(content.layout("x", content.item_body(item)))
    assert table == {
        "type": "table",
        "caption": None,
        "header": ["Field", "Value"],
        "rows": [["SKU", item.sku], ["Pages", str(item.pages)], ["Year", str(item.year)]],
        "row_headers": False,
    }


@pytest.mark.parametrize(
    ("site", "name"),
    [
        ("books-toscrape", "book-a-light-in-the-attic.html"),
        ("books-toscrape", "book-sapiens.html"),
    ],
)
def test_fixture_table_round_trips_with_the_same_cells(site: str, name: str) -> None:
    html = (FIXTURES / site / name).read_text("utf-8")
    [table] = table_blocks(html)
    assert round_trip(table) == table


def test_testsite_table_round_trips() -> None:
    [table] = table_blocks(content.layout("x", content.item_body(content.item(1))))
    assert round_trip(table) == table


# ---------------------------------------------------------------- merged cells


def test_colspan_and_rowspan_are_expanded_to_a_full_grid() -> None:
    table = parse(
        """<table>
        <thead>
          <tr><th rowspan="2">Title</th><th colspan="2">Price</th></tr>
          <tr><th>Excl. tax</th><th>Incl. tax</th></tr>
        </thead>
        <tbody>
          <tr><td>A</td><td colspan="2">£10</td></tr>
          <tr><td rowspan="2">B</td><td>1</td><td>2</td></tr>
          <tr><td>3</td><td>4</td></tr>
        </tbody></table>"""
    )
    assert table is not None
    assert table["header"] == ["Title", "Price / Excl. tax", "Price / Incl. tax"]
    assert table["rows"] == [["A", "£10", "£10"], ["B", "1", "2"], ["B", "3", "4"]]


def test_rowspan_zero_reaches_the_end_of_its_group_only() -> None:
    table = parse(
        """<table>
        <thead><tr><th rowspan="0">H</th><th>X</th></tr></thead>
        <tbody>
          <tr><td rowspan="0">all</td><td>1</td></tr>
          <tr><td>2</td></tr>
          <tr><td>3</td></tr>
        </tbody></table>"""
    )
    assert table is not None
    assert table["header"] == ["H", "X"]
    assert table["rows"] == [["all", "1"], ["all", "2"], ["all", "3"]]


def test_rowspan_past_the_last_row_is_cut_off() -> None:
    table = parse("<table><tr><td rowspan='9'>a</td><td>b</td></tr><tr><td>c</td></tr></table>")
    assert table is not None
    assert table["rows"] == [["a", "b"], ["a", "c"]]


def test_short_rows_are_padded_to_the_full_width() -> None:
    table = parse("<table><tr><th>a</th><th>b</th><th>c</th></tr><tr><td>1</td></tr></table>")
    assert table is not None
    assert table["rows"] == [["1", "", ""]]


@pytest.mark.parametrize(
    ("attrs", "expected_width"),
    [
        ('colspan="abc"', 2),  # invalid: 1
        ('colspan="0"', 2),  # colspan 0 is treated as 1
        ('colspan="2px"', 3),  # leading digits, as browsers read it
        ('colspan="99999999"', MAX_COLUMNS),  # clamped, no memory blow-up
    ],
)
def test_span_attributes_are_read_like_a_browser(attrs: str, expected_width: int) -> None:
    table = parse(f"<table><tr><td {attrs}>x</td><td>y</td></tr></table>")
    assert table is not None
    assert len(table["rows"][0]) == expected_width


def test_overlapping_cells_keep_the_first() -> None:
    table = parse(
        "<table><tr><td>a</td><td rowspan='2'>b</td></tr><tr><td colspan='2'>c</td></tr></table>"
    )
    assert table is not None
    assert table["rows"] == [["a", "b"], ["c", "b"]]


# ---------------------------------------------------------------- headers


def test_header_from_leading_th_rows_without_thead() -> None:
    table = parse("<table><tr><th>k</th><th>v</th></tr><tr><td>1</td><td>2</td></tr></table>")
    assert table is not None
    assert table["header"] == ["k", "v"]
    assert table["rows"] == [["1", "2"]]


def test_matrix_with_column_and_row_headers() -> None:
    table = parse(
        """<table>
        <tr><td></td><th>2023</th><th>2024</th></tr>
        <tr><th>Books</th><td>10</td><td>12</td></tr>
        <tr><th>Authors</th><td>4</td><td>5</td></tr></table>"""
    )
    assert table is not None
    assert table["header"] == ["", "2023", "2024"]  # empty corner cell
    assert table["row_headers"] is True
    assert table["rows"] == [["Books", "10", "12"], ["Authors", "4", "5"]]


def test_table_of_only_th_cells_uses_the_first_row_as_header() -> None:
    table = parse("<table><tr><th>a</th></tr><tr><th>b</th></tr></table>")
    assert table is not None
    assert table["header"] == ["a"]
    assert table["rows"] == [["b"]]
    assert table["row_headers"] is False  # a label with no data next to it


def test_caption_is_the_tables_own() -> None:
    table = parse(
        "<table><caption> Prices  ( 2024 ) </caption><tr><td>a</td><td>b</td></tr></table>"
    )
    assert table is not None
    assert table["caption"] == "Prices (2024)"


def test_cell_text_is_tidied_and_markup_free() -> None:
    table = parse(
        "<table><tr><td><a href='/a'>Austen</a> , <em>Jane</em></td>"
        "<td>line<br>break</td></tr></table>"
    )
    assert table is not None
    assert table["rows"] == [["Austen, Jane", "line break"]]


def test_empty_rows_and_columns_are_dropped() -> None:
    table = parse(
        "<table><tr><th>a</th><th></th><th>b</th></tr>"
        "<tr><td>1</td><td> </td><td>2</td></tr><tr><td></td><td></td><td></td></tr></table>"
    )
    assert table is not None
    assert table["header"] == ["a", "b"]
    assert table["rows"] == [["1", "2"]]


def test_table_without_text_is_skipped() -> None:
    assert parse("<table><tr><td> </td><td></td></tr></table>") is None
    assert parse("<table></table>") is None


# ---------------------------------------------------------------- layout tables


@pytest.mark.parametrize(
    "markup",
    [
        "<table role='presentation'><tr><td>a</td><td>b</td></tr></table>",
        "<table role='none'><tr><td>a</td><td>b</td></tr></table>",
        "<table><tr><td>only cell</td></tr></table>",
        "<table><tr><td><table><tr><td>a</td><td>b</td></tr></table></td></tr></table>",
    ],
)
def test_layout_tables_are_recognised(markup: str) -> None:
    assert is_layout_table(first_table(markup))


def test_data_table_is_not_layout() -> None:
    assert not is_layout_table(first_table("<table><tr><td>a</td><td>b</td></tr></table>"))


def test_layout_table_content_is_walked_and_inner_data_table_kept() -> None:
    blocks = clean_html(
        """<main><table role="presentation"><tr>
          <td><h2>Sidebar-free layout</h2><p>Text in a layout cell.</p></td>
          <td><table><tr><th>k</th><th>v</th></tr><tr><td>1</td><td>2</td></tr></table></td>
        </tr></table></main>"""
    ).blocks
    assert blocks == [
        {"type": "heading", "level": 2, "text": "Sidebar-free layout"},
        {"type": "paragraph", "text": "Text in a layout cell."},
        {"type": "table", "caption": None, "header": ["k", "v"], "rows": [["1", "2"]],
         "row_headers": False},
    ]  # fmt: skip


def test_nested_table_rows_stay_out_of_the_outer_grid() -> None:
    outer = first_table(
        "<table><tr><td>a</td><td>b<table><tr><td>x</td><td>y</td></tr></table></td></tr>"
        "<tr><td>c</td><td>d</td></tr></table>"
    )
    table = parse_table(outer)
    assert table is not None
    assert len(table["rows"]) == 2  # the inner row is not an outer row


# ---------------------------------------------------------------- round trips


def test_round_trip_keeps_escaped_text() -> None:
    block = {
        "type": "table",
        "caption": "<b> & co",
        "header": ["a < b", "c & d"],
        "rows": [["<script>", "&amp;"]],
        "row_headers": False,
    }
    assert round_trip(block) == block


def _word(rng: random.Random) -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789<>&\"'£é"
    return "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 8)))


def _cell(rng: random.Random) -> str:
    return " ".join(_word(rng) for _ in range(rng.randint(1, 3)))


@pytest.mark.parametrize("seed", range(200))
def test_random_tables_round_trip(seed: int) -> None:
    """Any block the parser can produce renders to HTML that parses back to it."""
    rng = random.Random(seed)  # noqa: S311 (seeded test data, not security)
    width, height = rng.randint(2, 6), rng.randint(1, 8)
    has_header = rng.random() < 0.6
    block = {
        "type": "table",
        "caption": _cell(rng) if rng.random() < 0.3 else None,
        "header": [_cell(rng) for _ in range(width)] if has_header else [],
        "rows": [[_cell(rng) for _ in range(width)] for _ in range(height)],
        "row_headers": rng.random() < 0.4,
    }
    assert round_trip(block) == block
