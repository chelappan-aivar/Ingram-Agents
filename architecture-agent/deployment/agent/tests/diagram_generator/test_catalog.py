"""Catalog structure tests: shapes vs aws4-shapes.json, colours vs the vendored fixer, validation, prompt section."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import yaml

from diagram_generator.catalog.loader import (
    COMPONENTS_PATH, KINDS, STYLES, Catalog, CatalogError, load_catalog, normalise,
)
from diagram_generator.config import Settings

SETTINGS = Settings()
SHAPES_PATH = SETTINGS.aws4_shapes_path
FIXER_PATH = SETTINGS.plugin_lib_dir / "fix_icon_colors.py"
CATS = json.loads(SHAPES_PATH.read_text(encoding="utf-8"))["categories"]
# categories where the vendored fixer deliberately differs from aws4-shapes.json fillColor (fixer wins)
FIXER_RECOLOURED = {"iot", "developer_tools"}


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return load_catalog()


@pytest.fixture(scope="module")
def fixer():
    """Import the vendored fixer by file path (its main() is not executed).

    The fixer annotates with `ET.ElementTree`, which defusedxml 0.7.1 no longer exports, so importing it fails
    at def time. We only need its colour tables, so the missing name is supplied for the import (and restored).
    """
    import defusedxml.ElementTree as dET
    import xml.etree.ElementTree as stdET

    mp = pytest.MonkeyPatch()
    if not hasattr(dET, "ElementTree"):
        mp.setattr(dET, "ElementTree", stdET.ElementTree, raising=False)
    try:
        spec = importlib.util.spec_from_file_location("fix_icon_colors_vendored", FIXER_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        mp.undo()
    return mod


def _json_category(shape: str) -> str | None:
    for key, val in CATS.items():
        if key not in ("sub_resources", "groups") and shape in val["shapes"]:
            return key
    return None


# ----------------------------------------------------------------------------- shapes
def test_size_is_reasonable(catalog):
    assert 120 <= len(catalog.canonical_services()) <= 200
    assert len(catalog.entries()) > len(catalog.canonical_services())     # generic entries exist


def test_every_shape_exists_in_right_list(catalog):
    assert SHAPES_PATH.exists()
    assert catalog.check_shapes(SHAPES_PATH) == []


def test_resource_icons_are_not_sub_resources_or_groups(catalog):
    sub = set(CATS["sub_resources"]["shapes"])
    groups = set(CATS["groups"]["shapes"])
    for e in catalog.entries():
        if e.style == "resource_icon":
            assert _json_category(e.shape) is not None, e
            assert e.shape not in groups, e
        else:
            assert e.shape in sub, e


def test_shapes_are_not_group_shapes_or_broken_names(catalog, fixer):
    renames = {k.removeprefix("mxgraph.aws4.") for k in fixer.SHAPE_RENAMES}
    for e in catalog.entries():
        assert e.shape not in renames, f"{e.name}: {e.shape} is a name the fixer renames"
        assert not e.shape.startswith("group_"), e


def test_styles_and_kinds_are_valid(catalog):
    for e in catalog.entries():
        assert e.style in STYLES and e.kind in KINDS


def test_category_keys_match_json_or_fixer_override(catalog, fixer):
    """resource_icon entries sit in their aws4-shapes.json category (and use its fill) unless the fixer says otherwise."""
    cats = catalog.categories()
    for e in catalog.entries():
        if e.style != "resource_icon" or e.category == "general":
            continue
        if f"mxgraph.aws4.{e.shape}" in fixer.SHAPE_TO_FILL or f"mxgraph.aws4.{e.shape}" in fixer.SHAPE_TO_CONTAINER:
            continue                                       # covered by the fixer cross-check
        jc = _json_category(e.shape)
        assert e.category == jc, f"{e.name}: category {e.category} but the shape is in {jc}"
        if jc not in FIXER_RECOLOURED:
            assert cats[jc].fill_color.upper() == CATS[jc]["fillColor"].upper(), (e.name, jc)


def test_every_category_row_is_used(catalog):
    used = {e.category for e in catalog.entries()}
    assert used == set(catalog.categories()), set(catalog.categories()) ^ used


# ----------------------------------------------------------------------------- fixer colour cross-check
def test_colours_agree_with_vendored_fixer(catalog, fixer):
    """The fixer overwrites icon fill and container tint/stroke for the shapes in its tables: ours must match."""
    cats = catalog.categories()
    checked = 0
    for e in catalog.entries():
        key = f"mxgraph.aws4.{e.shape}"
        row = cats[e.category]
        if key in fixer.SHAPE_TO_FILL:
            assert row.fill_color.upper() == fixer.SHAPE_TO_FILL[key].upper(), f"{e.name}: fill vs fixer"
            checked += 1
        if key in fixer.SHAPE_TO_CONTAINER:
            tint, stroke = fixer.SHAPE_TO_CONTAINER[key]
            assert (row.tint_color.upper(), row.stroke_color.upper()) == (tint.upper(), stroke.upper()), f"{e.name}: container vs fixer"
            checked += 1
    assert checked >= 40        # the catalog really covers the fixer's tables


@pytest.mark.parametrize("name, shape, fill, tint, stroke", [
    ("Amazon S3", "s3", "#3F8624", "#E8F5E9", "#3F8624"),
    ("AWS Lambda", "lambda", "#ED7100", "#FFF2E8", "#ED7100"),
    ("Amazon DynamoDB", "dynamodb", "#C925D1", "#F5E6F7", "#C925D1"),
    ("Amazon API Gateway", "api_gateway", "#8C4FFF", "#EDE7F6", "#8C4FFF"),
    ("Elastic Load Balancing", "elastic_load_balancing", "#8C4FFF", "#EDE7F6", "#8C4FFF"),
    ("AWS CodePipeline", "codepipeline", "#8C4FFF", "#EDE7F6", "#8C4FFF"),
    ("AWS IoT Core", "iot_core", "#1A9C37", "#E8F5E9", "#1A9C37"),
    ("IoT Device", "sensor", "#1A9C37", "#E8F5E9", "#1A9C37"),
    ("Amazon SQS", "sqs", "#E7157B", "#FCE4EC", "#E7157B"),
    ("AWS Systems Manager", "systems_manager", "#E7157B", "#FCE4EC", "#E7157B"),
    ("Amazon Cognito", "cognito", "#DD344C", "#FFEBEE", "#DD344C"),
    ("Amazon Bedrock AgentCore", "bedrock_agentcore", "#01A88D", "#E0F2F1", "#01A88D"),
    ("Amazon Kinesis Data Streams", "kinesis_data_streams", "#8C4FFF", "#EDE7F6", "#8C4FFF"),
])
def test_sample_icon_colours_match_fixer_tables(catalog, fixer, name, shape, fill, tint, stroke):
    icon = catalog.resolve(name, name, "aws_service")
    assert (icon.shape, icon.fill_color, icon.tint_color, icon.stroke_color) == (shape, fill, tint, stroke)
    key = f"mxgraph.aws4.{shape}"
    assert fixer.SHAPE_TO_FILL.get(key, fill).upper() == fill.upper()
    assert fixer.SHAPE_TO_CONTAINER.get(key, (tint, stroke)) == (tint, stroke)


# ----------------------------------------------------------------------------- uniqueness
def test_canonical_names_and_synonyms_are_unique(catalog):
    names = [e.name for e in catalog.entries()]
    assert len(names) == len(set(names))
    keys: dict[str, str] = {}
    for e in catalog.entries():
        for text in (e.name, *e.synonyms):
            k = normalise(text)
            assert keys.setdefault(k, e.name) == e.name, f"{text!r} ({e.name}) collides with {keys[k]}"


def test_synonyms_are_lowercase_and_not_stopwords(catalog):
    stop = {"api", "queue", "topic", "storage", "stream", "cache", "service", "data", "function", "server", "client",
            "cloud", "network", "file", "log", "logs", "event", "events", "job", "jobs", "table", "bucket", "model"}
    for e in catalog.entries():
        for s in e.synonyms:
            assert s == s.lower() and normalise(s), (e.name, s)
            assert normalise(s) not in stop, (e.name, s)


def _write_yaml(tmp_path: Path, mutate) -> Path:
    data = yaml.safe_load(COMPONENTS_PATH.read_text(encoding="utf-8"))
    mutate(data)
    p = tmp_path / "components.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return p


def _rel(tmp_path: Path) -> Path:
    from diagram_generator.catalog.loader import RELATIONSHIPS_PATH
    return RELATIONSHIPS_PATH


def test_loader_rejects_synonym_collision(tmp_path):
    def mutate(d):
        d["components"][-1]["synonyms"] = list(d["components"][-1].get("synonyms") or []) + ["s3"]
    with pytest.raises(CatalogError, match="collision"):
        Catalog.from_files(_write_yaml(tmp_path, mutate), _rel(tmp_path))


def test_loader_rejects_duplicate_canonical_name(tmp_path):
    def mutate(d):
        d["components"].append(dict(d["components"][-1], synonyms=["zzz unique"]))
    with pytest.raises(CatalogError, match="duplicate"):
        Catalog.from_files(_write_yaml(tmp_path, mutate), _rel(tmp_path))


def test_loader_rejects_unknown_category_style_kind_and_fallback(tmp_path):
    for field, value, pattern in [("category", "nope", "unknown category"), ("style", "nope", "unknown style"),
                                  ("kind", "nope", "unknown kind")]:
        def mutate(d, field=field, value=value):
            d["components"][-1][field] = value
        with pytest.raises(CatalogError, match=pattern):
            Catalog.from_files(_write_yaml(tmp_path, mutate), _rel(tmp_path))

    def bad_fallback(d):
        d["fallbacks"]["actor"] = "No Such Entry"
    with pytest.raises(CatalogError, match="fallbacks"):
        Catalog.from_files(_write_yaml(tmp_path, bad_fallback), _rel(tmp_path))


def test_loader_rejects_bad_verbs(tmp_path):
    from diagram_generator.catalog.loader import COMPONENTS_PATH as C
    p = tmp_path / "rel.yaml"
    p.write_text(yaml.safe_dump({"groups": {"g": [{"verb": "one two three four five", "synonyms": []}]}}))
    with pytest.raises(CatalogError, match="words"):
        Catalog.from_files(C, p)
    p.write_text(yaml.safe_dump({"groups": {"g": [{"verb": "writes to", "synonyms": ["saves"]},
                                                  {"verb": "saves", "synonyms": []}]}}))
    with pytest.raises(CatalogError, match="collision"):
        Catalog.from_files(C, p)


def test_load_catalog_is_cached():
    assert load_catalog() is load_catalog(SETTINGS)


# ----------------------------------------------------------------------------- relationships
def test_relationship_verbs(catalog):
    verbs = catalog.relationship_verbs()
    assert 25 <= len(verbs) <= 40
    assert len(verbs) == len(set(verbs))
    assert all(len(v.split()) <= 4 for v in verbs)
    assert catalog.normalise_verb("Reads From") == "reads from"
    assert catalog.normalise_verb("fetches from") == "reads from"
    assert catalog.normalise_verb("totally unknown verb") is None


# ----------------------------------------------------------------------------- prompt section
def test_prompt_section_is_compact_and_complete(catalog):
    text = catalog.extractor_prompt_section()
    assert len(text) < 8000
    for name in catalog.canonical_services():
        assert name in text, name
    for verb in catalog.relationship_verbs():
        assert verb in text, verb
    assert "Amazon S3 (" in text and "simple storage service" in text
    assert "service=null" in text
    assert text.rstrip().splitlines()[-1].startswith("Non-AWS items")      # generic block comes last
    assert "Users (actor)" in text and "Generic Database (data_store)" in text


def test_canonical_services_are_aws_only(catalog):
    names = catalog.canonical_services()
    assert "Amazon S3" in names
    for generic in ("Users", "Internet", "Generic Database", "IoT Device", "Generic AWS Service", "External API"):
        assert generic not in names
