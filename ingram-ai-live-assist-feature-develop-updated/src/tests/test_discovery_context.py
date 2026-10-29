"""The engagement context Live Assist sends with the Discovery documents (app/discovery_context.py)."""

from __future__ import annotations

from app.discovery_context import build_context, crm_url, header

OPP = "11111111-1111-4111-8111-111111111111"
ROW = {
    "intake_opportunity_id": OPP, "name": "DC exit", "customer": "Woodgrove Bank", "seller": "Priya Nair",
    "estimated_value_usd": 180000.0, "checked_at": "2026-10-27T10:00:00Z",
    "decision": {"status": "decided", "program": "MAP", "total_usd": 54000, "arr_usd": 90000, "rationale": "Migration"},
}
VIEWS = [
    {"session_id": "call-b", "title": "Follow-up", "created_at": "2026-10-29T09:00:00Z",
     "turns": [{"speaker": "cx", "speaker_name": "Dana Cole", "text": "We need DR."}]},
    {"session_id": "call-a", "title": "Kickoff", "created_at": "2026-10-20T09:00:00Z",
     "minutes": {"minutes": {"summary": "Agreed scope " + "x" * 400}},
     "turns": [{"speaker": "ba", "speaker_name": "Hannah Clarke", "text": "Welcome."},
               {"speaker": "cx", "speaker_name": "Dana Cole", "text": "Thanks."}]},
]
DOCS = [{"name": "01_a_transcript.txt", "session_id": "call-a"}, {"name": "01_a_minutes.md", "session_id": "call-a"}]


def test_header_has_known_fields_only():
    h = header(ROW, "https://org.crm.dynamics.com")
    assert h["customer"] == "Woodgrove Bank" and h["seller"] == "Priya Nair" and h["aws_program"] == "MAP"
    assert h["opportunity_size_usd"] == 180000.0 and h["opportunity_size_basis"] == "CRM estimated value"
    assert h["crm_url"].startswith("https://org.crm.dynamics.com/main.aspx?") and OPP in h["crm_url"]
    bare = header({**ROW, "seller": None, "estimated_value_usd": None}, "")
    assert "seller" not in bare and "crm_url" not in bare
    assert bare["opportunity_size_usd"] == 90000.0 and bare["opportunity_size_basis"] == "Expected AWS ARR (Intake)"


def test_crm_link_must_be_https():
    assert crm_url({**ROW, "crm_url": "javascript:alert(1)"}, "") is None
    assert crm_url({**ROW, "crm_url": "https://crm.example.com/x"}, "") == "https://crm.example.com/x"
    assert crm_url(ROW, "http://insecure.example.com") is None


def test_participants_are_grouped_by_side_without_duplicates():
    ctx = build_context(ROW, VIEWS, DOCS)
    sides = {p["name"]: p["side"] for p in ctx["participants"]}
    assert sides == {"Priya Nair": "ingram", "Dana Cole": "customer", "Hannah Clarke": "partner"}


def test_calls_are_oldest_first_with_short_summaries():
    calls = build_context(ROW, VIEWS, DOCS)["calls"]
    assert [c["title"] for c in calls] == ["Kickoff", "Follow-up"]
    assert calls[0]["date"] == "2026-10-20" and len(calls[0]["summary"]) <= 321
    assert calls[0]["documents"] == ["01_a_transcript.txt", "01_a_minutes.md"]
    assert calls[1]["summary"] == "We need DR."   # no minutes: the first line spoken


def test_report_summary_carries_executive_summary_and_pain_points():
    from app.discovery_link import summarise_report
    report = {"meta": {"generated_at": "2026-10-29T10:00:00Z"},
              "use_cases": [{"id": "UC-1", "title": "DR", "category": "LATENT", "confidence": "high", "rank": 1}],
              "what_we_heard": {"pain_points": [{"text": "Backups are slow"}, {"text": ""}]},
              "gaps_and_open_questions": [{"question": "Who owns DR?", "status": "open"},
                                          {"question": "Done?", "status": "resolved"}]}
    out = summarise_report(report, {"executive_summary": ["a", "b", "c", "d"]})
    assert out["executive_summary"] == ["a", "b", "c"]
    assert out["pain_points"] == ["Backups are slow"]
    assert out["open_questions"] == ["Who owns DR?"] and out["open_question_count"] == 1
    assert summarise_report(report)["executive_summary"] == []
