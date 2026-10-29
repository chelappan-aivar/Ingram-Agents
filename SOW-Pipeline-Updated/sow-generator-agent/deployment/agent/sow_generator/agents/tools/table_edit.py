"""Locating the original table for a table edit and rejecting edits that drop rows."""

import json
from typing import Any, Final

TABLE_JSON_PREFIX: Final = "TABLE_JSON:"

type Table = list[list[Any]]


def is_table_edit(replace: str) -> bool:
    return replace.startswith(TABLE_JSON_PREFIX)


def parse_table_json(replace: str) -> Table | None:
    """The table rows carried after the `TABLE_JSON:` prefix, or None when unparseable."""
    try:
        parsed = json.loads(replace[replace.index(TABLE_JSON_PREFIX) + len(TABLE_JSON_PREFIX) :])
    except ValueError:
        return None
    return parsed if isinstance(parsed, list) else None


def _tables(sections_map: dict[str, Any]) -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    for section in sections_map.values():
        if isinstance(section, dict):
            tables.extend(t for t in (section.get("tables") or []) if isinstance(t, dict))
    return tables


def _normalised_header(row: list[Any]) -> list[str]:
    return [str(cell).strip().lower() for cell in row]


def find_original_table(
    sections_map: dict[str, Any], *, table_tag: str, before_block_id: str, replace: str
) -> Table | None:
    """The table being replaced: by tag or anchor id, else by matching the header row."""
    tables = _tables(sections_map)
    for table in tables:
        if (table_tag and table.get("tag") == table_tag) or (
            before_block_id and table.get("before_para_id") == before_block_id
        ):
            data: Table | None = table.get("data")
            if data is not None:
                return data

    new_data = parse_table_json(replace)
    new_header = _normalised_header(new_data[0]) if new_data and new_data[0] else []
    if not new_header:
        return None
    for table in tables:
        table_data: Table = table.get("data") or []
        if table_data and _normalised_header(table_data[0]) == new_header:
            return table_data
    return None


def partial_edit_rejection(old_table: Table, replace: str) -> str | None:
    """An error message when the edit silently dropped rows, else None.

    Fewer rows are allowed when every new row exists verbatim in the original table, which means
    the model intentionally deleted the others.
    """
    new_data = parse_table_json(replace)
    if new_data is None or not 0 < len(new_data) < len(old_table):
        return None
    old_rows = [tuple(row) for row in old_table]
    if all(tuple(row) in old_rows for row in new_data):
        return None
    return (
        f"ERROR: Partial table edit rejected. The original table has {len(old_table)} rows "
        f"but you only provided {len(new_data)} rows and some rows don't match the original. "
        f"You MUST include ALL {len(old_table)} rows in the TABLE_JSON, then remove only the rows "
        "you want deleted. Re-read the section, copy every row, apply your changes, "
        "then call edit() again."
    )
