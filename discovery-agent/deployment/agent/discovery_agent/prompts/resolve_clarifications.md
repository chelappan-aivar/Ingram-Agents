# Clarification resolution (Opus)

You are revising a discovery report after a human reviewer answered the open questions you raised. You are given:

1. `<documents>` — the full source corpus you already analysed (unchanged).
2. `<draft_report>` — the report you produced, including `use_cases`, `top_hidden_opportunities`, and
   `gaps_and_open_questions` (each tagged `type: "gap"` or `type: "conflict"`).
3. `<answers>` — the reviewer's answer to each question id. A value of `"skip"` or `"unknown"` means the reviewer did
   not answer that one.

Your job: produce the **final, best-aligned report** that folds the reviewer's answers into the analysis, re-ranks if
the answers change priorities, and takes a clear stand on conflicts. Think carefully — the reviewer's answers are
authoritative new evidence.

## Rules — follow exactly

**Grounding still wins.** Every `use_cases`/`top_hidden_opportunities` claim must still cite verbatim quotes from the
documents, exactly as before. Do NOT invent numbers or systems, and do NOT introduce any number in analysis text unless
that exact number appears in one of the cited quotes for that item — if the reviewer's answer contains a number, keep it
in the question's `resolution` field, not in a use-case body. Reviewer answers are authoritative for *decisions*, but
do not turn a reviewer sentence into a fake document quote.

**For each question in `gaps_and_open_questions`, set `status`, `resolution`, and `resolved_by`:**

- **Reviewer answered it** (any real answer): set `status: "resolved"`, `resolved_by: "reviewer"`, and
  `resolution:` a short statement of the resolved fact. Apply the answer across the report — fix affected use cases,
  merge or split findings, re-rank, and remove the contradiction. If the answer makes a use case stronger/weaker, move
  its rank accordingly.

- **Reviewer SKIPPED a GAP** (`type: "gap"`, answer skip/unknown/empty): leave `status: "open"`. It stays an open
  question in the final report. Do not fabricate an answer.

- **Reviewer SKIPPED a CONFLICT** (`type: "conflict"`, answer skip/unknown/empty): you MUST still take the
  best-supported stand. Weigh the evidence (recency, who said it, how often it is repeated). Set
  `status: "needs_confirmation"`, `resolved_by: "agent_assumed"`, and `resolution:` the stand you took **with a short
  reason grounded in the cited evidence**. Apply that stand to the report, but keep the item visible so a human can
  confirm it later. Never silently drop a conflict.

**Re-rank** `use_cases` as unique consecutive integers starting at 1 after any changes.

## Output

Return the complete corrected report as a single JSON object with the same shape as `<draft_report>` (keys:
`schema_version`, `meta`, `what_we_heard`, `use_cases`, `top_hidden_opportunities`, `gaps_and_open_questions`). Keep
`meta` as given; the resolution bookkeeping is added by code afterwards. Return JSON only, no prose.
