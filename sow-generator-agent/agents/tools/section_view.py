"""Read-only editor tools over the document's sections."""

import json
from typing import Any

from strands import tool


def flatten_sections(sections_map: dict[str, Any]) -> dict[str, str]:
    """Render each structured section as plain text the model can read.

    Blocks carry their id as `[id:<id>]` so the model can reference them, and tables carry the
    anchors an edit needs (`tag` and `before_para_id`).
    """
    flat: dict[str, str] = {}
    for heading, section in sections_map.items():
        if isinstance(section, str):
            flat[heading] = section
            continue
        parts: list[str] = []
        for paragraph in section.get("paragraphs", []):
            if not isinstance(paragraph, dict):
                parts.append(str(paragraph))
                continue
            text = paragraph.get("text", "")
            style = paragraph.get("style", "")
            block_id = paragraph.get("block_id", paragraph.get("id", ""))
            # Render the style as a markdown prefix so the model sees the structure.
            is_bullet = "listParagraph" in style or "bullet" in style.lower()
            prefix = "- " if is_bullet else ""
            parts.append(f"{prefix}{text}  [id:{block_id}]")
        for table in section.get("tables", []):
            data = table.get("data", []) if isinstance(table, dict) else []
            before_id = table.get("before_para_id", "") if isinstance(table, dict) else ""
            tag = table.get("tag", "") if isinstance(table, dict) else ""
            if data:
                rows = ["\t".join(str(cell) for cell in row) for row in data]
                parts.append(
                    f"[TABLE tag:{tag} before_para_id:{before_id}] "
                    "\u2190 USE THIS before_para_id FOR TABLE EDITS\n"
                    + "\n".join(rows)
                    + f"\n[TABLE_DATA:{json.dumps(data)}]"
                )
        flat[heading] = "\n".join(parts)
    return flat


def build_read_tools(flat_sections: dict[str, str]) -> list[Any]:
    """The `read_section` and `search_document` tools, closed over the flattened sections."""

    @tool
    def read_section(heading: str) -> str:
        """Fetch the full text of a document section by its heading name.

        Args:
            heading: The exact heading name from the document outline.
        """
        if heading in flat_sections:
            return flat_sections[heading]
        for key, value in flat_sections.items():
            if key.lower().strip() == heading.lower().strip():
                return value
        available = ", ".join(flat_sections.keys())
        return f"Section '{heading}' not found. Available sections: {available}"

    @tool
    def search_document(query: str) -> str:
        """Search the document for paragraphs containing the query.

        Returns matches with their section location.

        Args:
            query: The text to search for across all sections.
        """
        query_lower = query.lower()
        results = []
        for heading, text in flat_sections.items():
            for paragraph in text.split("\n"):
                if query_lower in paragraph.lower() and paragraph.strip():
                    results.append(f"[{heading}]: {paragraph.strip()}")
        if not results:
            return f"No matches found for '{query}'."
        return "\n".join(results[:10])

    return [read_section, search_document]
