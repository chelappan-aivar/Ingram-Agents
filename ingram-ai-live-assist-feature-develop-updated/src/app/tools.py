"""Strands tools the model calls while processing a transcript chunk.

Tools validate input and record results in a per-request ``scratch`` dict;
the orchestrator applies them to the session after the agent run. Tool
docstrings are sent to the model as tool descriptions.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from strands import tool

from app.guidance import Guidance
from app.session_store import FIELD_SOURCE_AGENT, FIELD_SOURCE_GUIDANCE
from app.transcript import VALID_SPEAKERS

MAX_AGENT_ADDED_GAPS = 8
MAX_SUMMARY_CHARS = 600
MAX_RATIONALE_CHARS = 300
MAX_EVIDENCE_PER_CAPTURE = 10
MAX_RECALLS_PER_RUN = 2
MAX_RECALL_QUERY_CHARS = 300
RECALL_LEVELS = ("account", "opportunity", "both")

# Off while suggestions are limited to the general checklist: agent-added gaps
# could never be asked about.
ENABLE_AGENT_GAPS = False


@dataclass
class FieldDef:
    id: str
    label: str
    description: str
    required: bool
    source: str


def build_tools(
    *,
    scratch: dict,
    known_fields: dict[str, FieldDef],
    available_opportunity_types: list[str],
    opportunity_type_locked: bool,
    agent_added_count: int,
    load_specialised_fields: Callable[[str], list[FieldDef]],
    askable_field_ids: set[str] | None = None,
    require_target: bool = True,
    max_suggestions: int = 1,
    recent_questions: Iterable[str] = (),
    ask_new_fields: bool = False,
    turn_seqs: Iterable[int] = (),
    recall: Callable[[str, str], list[dict] | None] | None = None,
) -> list:
    """Build the tool set for one request.

    Args:
        scratch: Receives tool results: ``problem_summary``, ``opportunity_type``,
            ``captured_fields``, ``added_gaps``, ``dismissed_gaps``, ``suggestion``.
        known_fields: Tracked fields. Updated in place so later calls in the same
            request can reference newly added fields.
        available_opportunity_types: Specialised types the model may set.
        opportunity_type_locked: True once a type has been set for the session.
        agent_added_count: Agent-added gaps already in the session.
        load_specialised_fields: Returns the fields of a specialised type.
        askable_field_ids: Field ids a suggestion may target. None means no limit.
            Agent-added gaps are always allowed.
        require_target: If True, every suggestion must name a target field.
        max_suggestions: Most suggestions allowed in this request.
        recent_questions: Earlier suggestions; repeats of these are rejected.
        ask_new_fields: If True, fields added by set_opportunity_type in this
            request may be targeted.
        turn_seqs: Seqs of this request's turns; evidence must cite one of them.
        recall: Searches this call's long-term memory: ``(query, level)`` -> facts
            (``{"level", "text"}``), or None if memory is unavailable. The call's
            account and opportunity are bound in, so the model can't pick them.
            None leaves the recall tool out.
    """
    valid_seqs = set(turn_seqs)
    new_field_ids: set[str] = set()
    scratch.setdefault("captured_fields", {})
    scratch.setdefault("added_gaps", [])
    scratch.setdefault("dismissed_gaps", {})
    scratch.setdefault("suggestions", [])
    scratch.setdefault("memory_lookups", [])
    seen_questions = {_question_key(q) for q in recent_questions}

    @tool
    def update_problem_summary(summary: str) -> str:
        """Rewrite your running summary of the customer's problem.

        Call when the chunk changes what you know about the problem, its impact
        or the outcome the customer wants. 1-3 plain sentences, facts from the
        call only.

        Args:
            summary: The full updated summary. Replaces the previous one.
        """
        summary = summary.strip()
        if not summary:
            return "Summary must be non-empty."
        scratch["problem_summary"] = summary[:MAX_SUMMARY_CHARS]
        return "Problem summary updated."

    @tool
    def set_opportunity_type(opportunity_type: str) -> str:
        """Add a specialised checklist on top of the general one.

        Call only when the conversation clearly matches a listed specialised
        type. If none fits, don't call it.

        Args:
            opportunity_type: One of the specialised opportunity type ids.
        """
        if opportunity_type_locked or scratch.get("opportunity_type"):
            return "A specialised opportunity type is already set for this session."
        if opportunity_type not in available_opportunity_types:
            return (
                f"'{opportunity_type}' is not a known opportunity type. "
                f"Valid options: {available_opportunity_types}. If none fits, keep "
                "using the general checklist."
            )
        new_ids = []
        for fd in load_specialised_fields(opportunity_type):
            if fd.id not in known_fields:
                known_fields[fd.id] = fd
                new_ids.append(fd.id)
                new_field_ids.add(fd.id)
        scratch["opportunity_type"] = opportunity_type
        added = ", ".join(new_ids) or "none"
        return f"Opportunity type set to '{opportunity_type}'. New fields now tracked: {added}."

    @tool
    def record_captured_field(
        field_id: str, value: str, speaker: str, evidence_seqs: list[int] | None = None
    ) -> str:
        """Record that a tracked field was answered.

        Only for a clear answer. The BA asking about something is not an answer.
        "Not decided yet" from the customer is an answer.

        Args:
            field_id: Id of a tracked field.
            value: Short summary of what was said.
            speaker: Who said it, "ba" or "cx".
            evidence_seqs: The #seq numbers of the turns in this chunk that hold the answer.
        """
        if field_id not in known_fields:
            return f"'{field_id}' is not a tracked field. Tracked ids: {sorted(known_fields)}"
        if speaker not in VALID_SPEAKERS:
            return f"speaker must be one of {sorted(VALID_SPEAKERS)}"
        if not value.strip():
            return "value must be non-empty."
        # Citations outside this chunk can't be checked, so they are dropped.
        evidence = sorted(
            {s for s in (evidence_seqs or []) if isinstance(s, int) and s in valid_seqs}
        )[:MAX_EVIDENCE_PER_CAPTURE]
        scratch["captured_fields"][field_id] = {
            "value": value.strip(), "speaker": speaker, "evidence": evidence,
        }
        scratch["dismissed_gaps"].pop(field_id, None)
        return f"Recorded '{field_id}' as captured."

    @tool
    def add_gap(label: str, description: str, reason: str, required: bool = False) -> str:
        """Start tracking new information this customer's work depends on.

        Use for something specific the conversation revealed that isn't tracked
        yet, such as an integration, a regulation or a hard deadline. Not for
        generic sales questions, pitches or unrelated topics.

        Args:
            label: Short name, a few words.
            description: What needs to be found out.
            reason: What in the conversation makes it necessary.
            required: True if the work can't be scoped without it.

        Returns:
            The new field id, usable in later tool calls.
        """
        if agent_added_count + len(scratch["added_gaps"]) >= MAX_AGENT_ADDED_GAPS:
            return (
                f"Limit of {MAX_AGENT_ADDED_GAPS} added gaps reached; focus on the "
                "fields already tracked."
            )
        field_id = _slugify(label)
        if not field_id or not description.strip() or not reason.strip():
            return "label, description and reason must all be non-empty."
        if field_id in known_fields:
            return f"Already tracked as '{field_id}'."
        known_fields[field_id] = FieldDef(
            id=field_id,
            label=label.strip(),
            description=description.strip(),
            required=bool(required),
            source=FIELD_SOURCE_AGENT,
        )
        scratch["added_gaps"].append(field_id)
        return f"Added gap '{field_id}'."

    @tool
    def dismiss_gap(field_id: str, reason: str) -> str:
        """Stop tracking a field the conversation has made irrelevant.

        Allowed for optional fields and gaps you added. Required checklist
        fields can't be dismissed; capture the customer's answer instead.

        Args:
            field_id: Id of the tracked field.
            reason: What in the conversation makes it irrelevant.
        """
        fd = known_fields.get(field_id)
        if fd is None:
            return f"'{field_id}' is not a tracked field."
        if fd.required and fd.source == FIELD_SOURCE_GUIDANCE:
            return (
                f"'{field_id}' is a required checklist field and can't be dismissed. "
                "Capture the customer's answer instead (e.g. 'not applicable because ...')."
            )
        if not reason.strip():
            return "reason must be non-empty."
        if field_id in scratch["captured_fields"]:
            return f"'{field_id}' was captured this turn; not dismissing."
        scratch["dismissed_gaps"][field_id] = reason.strip()
        return f"Dismissed '{field_id}'."

    @tool
    def suggest_next_question(question: str, target_field_id: str = "", rationale: str = "") -> str:
        """Suggest a question for the BA to ask the customer.

        Call once per question, after the other tools, up to the number your
        instructions allow. Follow the WHAT, WHEN and HOW rules in your
        instructions; they say whether target_field_id is required.

        Write it the way a person says it out loud: plain words, one short
        sentence. No em or en dashes, no semicolons, no corporate phrasing, no
        lead-ins like "Great" or "It would be helpful to".

        Args:
            question: The question, as the BA would say it.
            target_field_id: Id of the open field the question fills, or "" for a
                follow-up that fills no field.
            rationale: One short sentence for the BA on what prompted the question.
        """
        suggestions = scratch["suggestions"]
        if len(suggestions) >= max_suggestions:
            return f"Limit of {max_suggestions} suggestion(s) for this request reached."
        question = plain_text(question)
        if not question:
            return "Question must be non-empty."
        if target_field_id and target_field_id not in known_fields:
            return f"'{target_field_id}' is not a tracked field. Tracked ids: {sorted(known_fields)}"

        key = _question_key(question)
        if key in seen_questions:
            return (
                "You already suggested this question. Ask a different, more specific "
                "question, or skip."
            )
        if target_field_id and any(s["target_field_id"] == target_field_id for s in suggestions):
            return f"You already suggested a question for '{target_field_id}' in this request."

        if askable_field_ids is not None:
            # Topics answered or dismissed earlier in this request are off limits.
            settled = set(scratch["captured_fields"]) | set(scratch["dismissed_gaps"])
            allowed = sorted(
                fid for fid, fd in known_fields.items()
                if fid not in settled
                and (
                    fid in askable_field_ids
                    or fd.source == FIELD_SOURCE_AGENT
                    or (ask_new_fields and fid in new_field_ids)
                )
            )
            if (target_field_id or require_target) and target_field_id not in allowed:
                return (
                    "You may only target these open topics: "
                    f"{allowed}. If none fits what was just said, skip the suggestion."
                )
        elif require_target and not target_field_id:
            return "Set target_field_id to the tracked field this question fills."

        suggestions.append({
            "question": question,
            "target_field_id": target_field_id or None,
            "rationale": plain_text(rationale)[:MAX_RATIONALE_CHARS] or None,
        })
        seen_questions.add(key)
        return "Suggestion recorded."

    @tool
    def recall_prior_context(query: str, level: str = "both") -> str:
        """Search what was learned in earlier calls with this customer.

        Use when the customer refers to an earlier conversation, or before
        suggesting a question on a topic the prior context in your instructions
        doesn't cover. Results were not said in this call: never capture a field
        from them.

        Args:
            query: What to look for, in a few words, e.g. "current hosting".
            level: "account" (the company), "opportunity" (this deal) or "both".
        """
        lookups = scratch["memory_lookups"]
        if len(lookups) >= MAX_RECALLS_PER_RUN:
            return f"Limit of {MAX_RECALLS_PER_RUN} lookups for this request reached. Continue without it."
        if level not in RECALL_LEVELS:
            return f"level must be one of {list(RECALL_LEVELS)}"
        query = " ".join(query.split())[:MAX_RECALL_QUERY_CHARS]
        if not query:
            return "query must be non-empty."
        started = time.monotonic()
        facts = recall(query, level)
        lookups.append({
            "query": query, "level": level, "hits": len(facts or []),
            "ok": facts is not None, "ms": int((time.monotonic() - started) * 1000),
        })
        if facts is None:
            return "Prior context is unavailable right now. Continue without it."
        if not facts:
            return "Nothing relevant is remembered from earlier calls."
        return "Remembered from earlier calls (not said in this call):\n" + "\n".join(
            f"- [{f['level']}] {f['text']}" for f in facts
        )

    tools = [update_problem_summary, set_opportunity_type, record_captured_field]
    if ENABLE_AGENT_GAPS:
        tools.append(add_gap)
    tools += [dismiss_gap, suggest_next_question]
    if recall is not None:
        tools.append(recall_prior_context)
    return tools


def field_defs_from_guidance(guidance: Guidance) -> list[FieldDef]:
    return [
        FieldDef(f.id, f.label, f.description, f.required, FIELD_SOURCE_GUIDANCE)
        for f in guidance.required_fields
    ]


def plain_text(text: str) -> str:
    """Replace typographic punctuation (dashes, curly quotes, ellipsis) with plain ASCII."""
    text = re.sub(r"\s*[\u2014\u2015]\s*", ", ", text)  # Em dash, horizontal bar.
    text = re.sub(r"\s+\u2013\s+", ", ", text)  # Spaced en dash used as a break.
    text = text.replace("\u2013", "-")  # En dash in ranges, e.g. 5-10.
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2026", "...")
    text = re.sub(r",\s*,", ",", text)
    text = re.sub(r"\s+([?.!,])", r"\1", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def _question_key(question: str) -> str:
    """Normalize a question for duplicate checks: lowercase letters and digits only."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", question.lower()).split())


def _slugify(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    return f"custom_{slug[:40]}" if slug else ""
