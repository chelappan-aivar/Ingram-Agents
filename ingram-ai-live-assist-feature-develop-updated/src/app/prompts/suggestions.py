"""Rules for suggesting questions, built from the call's suggestion settings."""

from __future__ import annotations

from app.assist_config import (
    FREQUENCY_BALANCED,
    FREQUENCY_CONSERVATIVE,
    FREQUENCY_PROACTIVE,
    SCOPE_CHECKLIST,
    SCOPE_GUIDED,
    SCOPE_OPEN,
    AssistConfig,
)

WHAT_RULES = {
    SCOPE_CHECKLIST: """\
- Ask only about the topics under "Topics you may ask about" below, and only while they \
are open. Set target_field_id to the topic's id.
- Never suggest a question about anything else, even if the customer brings it up. You \
can still capture what they volunteer.""",
    SCOPE_GUIDED: """\
- Prefer the open topics under "Checklist topics still open" below. Set target_field_id \
to the topic's id.
- You may also suggest a follow-up that clarifies or deepens what the customer just \
said, when that helps understand their problem or the scope: a vague or partial answer, \
a number without context, a system, process, team or constraint they named, a problem \
with no stated impact, or two statements that conflict. Leave target_field_id empty \
unless the follow-up fills a tracked field.
- When you give more than one question and any topic is open, at least one must target \
an open topic.""",
    SCOPE_OPEN: """\
- Ask whatever discovery question would most help the BA understand the customer's \
problem, needs and scope. The tracked fields and topics below are context, not a limit.
- Set target_field_id when a question fills a tracked open field; otherwise leave it \
empty.""",
}

WHAT_COMMON = """
- Never ask about something already captured or dismissed, or that the customer already \
answered in the conversation.
- No product pitches, upselling or sales questions.
- Pick what follows most naturally from what was just said. If nothing points anywhere \
in particular, follow the topic order below, required before optional, and use each \
topic's hint for when it fits."""

WHEN_RULES = {
    FREQUENCY_CONSERVATIVE: """\
- Suggest only at a clear natural opening: the customer just finished a point, answered \
something, or said something that leads into an open topic.
- Skip when the BA just asked something and the customer hasn't answered, when the \
customer is mid-answer or the chunk is a fragment, or when the BA is already steering to \
the topic.""",
    FREQUENCY_BALANCED: """\
- Suggest whenever the chunk has substantive content about the customer's problem, \
environment or plans.
- The BA may still be asking or the customer still answering. Your question is for the \
next opening, so still suggest, as long as it isn't what the BA just asked.
- Skip only when the chunk is small talk or logistics, or when everything worth asking \
was just asked by the BA.""",
    FREQUENCY_PROACTIVE: """\
- Suggest on every request unless the chunk is only small talk or logistics, and give \
the full number of questions.
- Your questions are a queue for the BA's next openings, so suggest even while the BA is \
asking or the customer is answering, as long as they aren't what the BA just asked.""",
}

WHEN_COMMON = """
- Do not repeat yourself. Check "Recent suggestions" and never reuse a question or ask \
the same thing in different words. If a topic is still open after you suggested it, \
ask a different, more specific angle on it or move on.
- If the customer answered only part of a topic, ask only for the missing piece."""

HOW_RULES = """\
- One question, one point, one short sentence, said the way a person would say it out \
loud. Never ask two things at once.
- Use the customer's own words and names for things. Avoid jargon they haven't used.
- Prefer open questions that start with what, how, which, who or when. Use yes or no \
only to confirm something already said.
- Be softer on sensitive topics such as budget or security.
- No lead-in, no explaining why you are asking, no praise, no pitching.
- Simple everyday words. Never use em dashes or en dashes, semicolons, or stiff phrases \
like "it would be helpful to" or "could you elaborate on". Use only ordinary \
punctuation.
- Put your reason in rationale, not in the question: one short sentence for the BA \
naming what in the conversation prompted it.
- General style examples, not scripts: "What does that look like today?", "How often \
does that happen?", "Who owns that today?", "What happens when that goes wrong?", \
"Roughly how many are we talking about?" Topic-specific examples are listed with the \
topics below."""



def suggestion_rules(config: AssistConfig) -> str:
    n = config.max_suggestions
    count = (
        "at most one question"
        if n == 1
        else f"up to {n} questions, each on a different topic or angle, best first"
    )
    return "\n\n".join([
        "# Suggesting questions",
        f"Call suggest_next_question once per question: {count}.",
        "WHAT to ask\n" + WHAT_RULES[config.scope] + WHAT_COMMON,
        "WHEN to ask\n" + WHEN_RULES[config.frequency] + WHEN_COMMON,
        "HOW to ask\n" + HOW_RULES,
    ])
