"""Every module of the package (and main.py) imports without the monolith's app.* packages."""

import importlib
import pkgutil
from pathlib import Path

import sow_generator


def test_every_module_imports():
    names = [m.name for m in pkgutil.walk_packages(sow_generator.__path__, "sow_generator.")]
    assert len(names) > 50
    for name in names:
        importlib.import_module(name)


def test_no_monolith_imports_left():
    root = Path(sow_generator.__file__).parent
    offenders = [str(p) for p in root.rglob("*.py")
                 if "from app." in p.read_text() or "import app." in p.read_text()]
    assert offenders == []


def test_resources_are_package_data():
    from sow_generator.repositories.resource_loader import RESOURCES_PACKAGE, read_resource_bytes, read_resource_text

    assert RESOURCES_PACKAGE == "sow_generator.resources"
    assert "[COMPANY_NAME]" in read_resource_text("prompts", "system_prompt.md")
    assert read_resource_bytes("assets", "ingram-micro-logo.png")[:8] == b"\x89PNG\r\n\x1a\n"


def test_templates_listed():
    from sow_generator.services.template_service import list_template_options

    ids = {t.id for t in list_template_options()}
    assert {"aiml/ai-ml", "devops/devops-standard"} <= ids


def test_main_imports_and_routes():
    import main

    assert set(main.ACTIONS) == {"ping", "templates", "template", "generate", "edit", "wa_review",
                                 "check", "export_docx"}
    assert main.invoke({"action": "ping"})["status"] == "completed"
    assert main.invoke({"action": "nope"})["status"] == "failed"
    assert main.invoke({"action": "template", "id": "aiml/ai-ml"})["markdown"].lstrip().startswith("<")
    missing = main.invoke({"action": "template", "id": "aiml/missing"})
    assert missing["status"] == "failed" and missing["status_code"] == 404
    bad = main.invoke({"action": "generate", "job_id": "x", "request": {}})
    assert bad["status"] == "failed" and bad["status_code"] == 400


def test_wa_review_action_is_synchronous_and_returns_a_review():
    """The Well-Architected stage runs before the diagram, so it must answer in one call."""
    import main

    result = main.invoke({"action": "wa_review", "request": {
        "sow_markdown": "# Scope of Work\n- Container platform using Amazon ECS, Amazon EKS or AWS Fargate\n",
    }})
    assert result["status"] == "completed"
    review = result["review"]
    assert review["can_proceed"] is False
    ids = {f["id"] for f in review["findings"]}
    assert "wa_operations_compute_decision_open" in ids
    assert review["summary"]["total"] == len(review["findings"])

    bad = main.invoke({"action": "wa_review", "request": {"sow_markdown": "  "}})
    assert bad["status"] == "failed" and bad["status_code"] == 400
