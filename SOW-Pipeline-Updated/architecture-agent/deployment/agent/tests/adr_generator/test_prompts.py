"""The system prompt: files present, assembled in order, inside the token budget, professional wording."""
import re

from adr_generator.config import Settings
from adr_generator.generator import PROMPT_FILES, TOOL_NAME, build_system_prompt, build_tool_spec, build_user_message

CHARS_PER_TOKEN = 4  # conservative estimate for English prose


def _text(name: str) -> str:
    return (Settings().prompts_dir / name).read_text(encoding="utf-8")


def test_all_prompt_files_exist():
    for name in PROMPT_FILES:
        assert (Settings().prompts_dir / name).is_file(), name


def test_skill_and_service_sheet_within_2000_tokens():
    assert len(_text("skill.md")) <= 2000 * CHARS_PER_TOKEN
    assert len(_text("services.md")) <= 2000 * CHARS_PER_TOKEN


def test_system_prompt_within_budget_and_ordered():
    prompt = build_system_prompt(Settings())
    assert len(prompt) <= 7500 * CHARS_PER_TOKEN
    starts = [prompt.index(_text(n).strip()[:60]) for n in PROMPT_FILES]
    assert starts == sorted(starts)


def test_professional_wording():
    prompt = build_system_prompt(Settings()).lower()
    for word in ("mvp", "demo", "mock", "dummy", "prototype"):
        assert not re.search(rf"\b{word}\b", prompt), word
    # "client" appears only in the instruction that forbids it
    assert not re.search(r"\bclients?\b", prompt.replace('never "client"', ""))


def test_tool_spec_and_user_message():
    spec = build_tool_spec()["toolSpec"]
    assert spec["name"] == TOOL_NAME
    props = spec["inputSchema"]["json"]["properties"]
    assert list(props)[:7] == ["title", "customer", "context", "region", "availability_zones",
                               "requirements", "decisions"]
    assert props["availability_zones"]["type"] == "array"
    msg = build_user_message("srs.md", "SRS body")
    assert '<document file="srs.md">' in msg and TOOL_NAME in msg


def test_availability_model_is_in_the_system_prompt():
    """The zone count is configuration, so it must reach the model from settings, not a prompt file."""
    multi = build_system_prompt(Settings(_env_file=None, AVAILABILITY_ZONES=3))
    assert "three Availability Zones" in multi
    single = build_system_prompt(Settings(_env_file=None, AVAILABILITY_ZONES=1))
    assert "one Availability Zone" in single
    assert "deliberate exception" in single and "highly available" not in single.split("Availability model")[1][:200]


def test_scope_block_binds_the_design_when_supplied():
    from adr_generator.scope import Scope

    scope = Scope(in_scope=["Amazon Bedrock claims triage"], out_of_scope=["Amazon Kinesis streaming"])
    prompt = build_system_prompt(Settings(_env_file=None), scope)
    assert "Agreed scope (authoritative)" in prompt
    msg = build_user_message("srs.md", "SRS body", scope)
    assert "<scope>" in msg and "Amazon Kinesis streaming" in msg
    # no scope supplied -> no scope block at all
    assert "<scope>" not in build_user_message("srs.md", "SRS body")
