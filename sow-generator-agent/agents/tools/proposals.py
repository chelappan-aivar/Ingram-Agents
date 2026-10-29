"""Editor tools that propose changes; the user accepts or rejects each one in the UI."""

from typing import Any

from strands import tool

from app.modules.sow_generator.agents.tools.table_edit import (
    find_original_table,
    is_table_edit,
    partial_edit_rejection,
)

type Proposal = dict[str, Any]

_ACCEPT_REJECT_NOTE = "The user will see Accept/Reject buttons."


def build_proposal_tools(sections_map: dict[str, Any], proposals: list[Proposal]) -> list[Any]:
    """The tools that append change proposals, closed over the document and the output list."""

    def reject(message: str) -> str:
        proposals.append({"type": "tool_result", "tool": "edit", "result": message})
        return message

    @tool
    def edit(
        block_id: str = "",
        replace: str = "",
        rationale: str = "",
        heading: str = "",
        find: str = "",
        before_block_id: str = "",
        table_tag: str = "",
        paragraph_id: str = "",
        before_para_id: str = "",
    ) -> str:
        """Propose replacing a specific block or table.

        Args:
            block_id: The id from [id:<id>] in read_section output. REQUIRED for text edits. Optional for table edits if before_block_id or table_tag is provided.
            replace: The replacement text. For table edits use TABLE_JSON: prefix with the COMPLETE table (all rows, all columns). The frontend replaces the entire table — partial rows will truncate the table.
            rationale: Brief explanation of the change.
            heading: The section heading (always provide).
            find: The verbatim block text (for display only — not used for lookup).
            before_block_id: For table edits — the id from [TABLE tag:xxx before_para_id:<id>].
            table_tag: For table edits — the tag from [TABLE tag:xxx ...]. ALWAYS provide this for table edits — it enables direct table lookup.
            paragraph_id: Deprecated alias for block_id.
            before_para_id: Deprecated alias for before_block_id.
        """  # noqa: E501 - the docstring is the tool description the model sees
        block_id = block_id or paragraph_id
        before_block_id = before_block_id or before_para_id
        table_edit = is_table_edit(replace)
        if not block_id and not table_edit:
            return reject(
                "ERROR: block_id is required for text edits. Read the section first and copy "
                "the [id:<id>] for the block you want to edit."
            )
        if not block_id and table_edit and not before_block_id and not table_tag:
            return reject(
                "ERROR: For table edits, provide before_block_id or table_tag from the "
                "[TABLE tag:xxx before_para_id:xxx] annotation in read_section output. "
                "Call read_section() first if you don't have it."
            )

        old_table = None
        if table_edit:
            old_table = find_original_table(
                sections_map,
                table_tag=table_tag,
                before_block_id=before_block_id,
                replace=replace,
            )
            rejection = partial_edit_rejection(old_table, replace) if old_table else None
            if rejection:
                return reject(rejection)
        proposals.append(
            {
                "type": "edit_proposal",
                "block_id": block_id,
                "find": find,
                "replace": replace,
                "rationale": rationale,
                "heading": heading,
                "before_block_id": before_block_id,
                "table_tag": table_tag,
                "old_table": old_table,
            }
        )
        return f"Edit proposed successfully. {_ACCEPT_REJECT_NOTE}"

    @tool
    def insert_section(after_heading: str, markdown: str, rationale: str = "") -> str:
        """Propose inserting a new section after the specified heading.

        Args:
            after_heading: The heading after which to insert the new section.
            markdown: The markdown content of the new section.
            rationale: Brief explanation of why this section is being added.
        """
        proposals.append(
            {
                "type": "insert_proposal",
                "after_heading": after_heading,
                "markdown": markdown,
                "rationale": rationale,
            }
        )
        return f"Insert proposed successfully. {_ACCEPT_REJECT_NOTE}"

    @tool
    def insert_after_paragraph(
        block_id: str = "", markdown: str = "", rationale: str = "", paragraph_id: str = ""
    ) -> str:
        """Propose inserting new content immediately after a specific block (mid-section insertion).

        Args:
            block_id: The unique id of the block after which to insert (from sections_map paragraphs[].block_id).
            markdown: The markdown content to insert.
            rationale: Brief explanation of why this content is being added.
            paragraph_id: Deprecated alias for block_id.
        """  # noqa: E501 - the docstring is the tool description the model sees
        proposals.append(
            {
                "type": "insert_after_paragraph_proposal",
                "block_id": block_id or paragraph_id,
                "markdown": markdown,
                "rationale": rationale,
            }
        )
        return f"Insert proposed successfully. {_ACCEPT_REJECT_NOTE}"

    @tool
    def delete_section(heading: str, rationale: str = "") -> str:
        """Propose deleting an entire section from the document.

        Args:
            heading: The exact heading name of the section to delete (from the document outline).
            rationale: Brief explanation of why this section should be removed.
        """
        proposals.append({"type": "delete_proposal", "heading": heading, "rationale": rationale})
        return f"Delete proposed successfully. {_ACCEPT_REJECT_NOTE}"

    return [edit, insert_section, insert_after_paragraph, delete_section]
