"""Component / relationship catalog (WP2). See ../../INTERFACES.md ("WP2: Catalog").

Maps service names found in documents to draw.io AWS4 icons (IconRef) and offers the edge-label verbs.

    catalog = load_catalog()
    catalog.resolve("Amazon S3", "Orders bucket", "data_store")   -> IconRef(shape="s3", matched=True)
    catalog.resolve(None, "Salesforce CRM", "external")           -> IconRef(shape="internet", matched=False)

`resolve` never raises. The YAML is validated once at load; problems raise CatalogError.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import yaml

from diagram_generator.contracts import ComponentKind, IconRef

CATALOG_DIR = Path(__file__).resolve().parent
COMPONENTS_PATH = CATALOG_DIR / "components.yaml"
RELATIONSHIPS_PATH = CATALOG_DIR / "relationships.yaml"

STYLES = ("resource_icon", "sub_resource", "general")
KINDS = ("aws_service", "external", "actor", "data_store", "generic")
MAX_VERB_WORDS = 4
_MAX_INPUT_CHARS = 300          # longer inputs are cut before matching
_DROP_TOKENS = {"amazon", "aws"}

# Prompt-section tuning (the section goes into an LLM prompt, keep it tight)
_PROMPT_MAX_SYNONYMS = 3
_PROMPT_MAX_SYNONYM_CHARS = 22


class CatalogError(ValueError):
    """The catalog YAML is invalid (duplicate names, synonym collision, unknown category, ...)."""


# ----------------------------------------------------------------------------- normalisation
def normalise(text: Any) -> str:
    """Lowercase alphanumeric tokens; drops "amazon"/"aws" and a trailing "service(s)" word.

    'Amazon S3' -> 's3';  'AWS Site-to-Site VPN' -> 'site to site vpn';  'Amazon OpenSearch Service' -> 'opensearch'
    """
    if text is None:
        return ""
    t = str(text)[:_MAX_INPUT_CHARS].lower().replace("&", " and ")
    tokens = [x for x in re.sub(r"[^a-z0-9]+", " ", t).split() if x not in _DROP_TOKENS]
    if len(tokens) > 1 and tokens[-1] in ("service", "services"):
        tokens = tokens[:-1]
    return " ".join(tokens)


# ----------------------------------------------------------------------------- data classes
@dataclass(frozen=True)
class Category:
    key: str
    label: str
    fill_color: str
    tint_color: str
    stroke_color: str


@dataclass(frozen=True)
class Entry:
    name: str
    shape: str
    style: str
    category: str
    kind: str
    synonyms: tuple[str, ...]
    exact: bool = False

    @property
    def is_aws(self) -> bool:
        """True for AWS services; False for the generic/non-AWS glyphs and the generic fallback."""
        return self.style != "general" and self.category != "general"


@dataclass(frozen=True)
class Verb:
    verb: str
    group: str
    synonyms: tuple[str, ...]


# ----------------------------------------------------------------------------- catalog
class Catalog:
    def __init__(self, components: dict[str, Any], relationships: dict[str, Any]):
        self._categories: dict[str, Category] = {}
        self._entries: list[Entry] = []
        self._by_name: dict[str, Entry] = {}
        self._index: dict[str, tuple[Entry, bool]] = {}    # normalised key -> (entry, exact_only)
        self._fallbacks: dict[str, Entry] = {}
        self._verbs: list[Verb] = []
        self._verb_index: dict[str, Verb] = {}
        self._max_ngram = 1
        self._build_components(components)
        self._build_relationships(relationships)

    # ------------------------------------------------------------------ construction
    @classmethod
    def from_files(cls, components_path: Path | str = COMPONENTS_PATH,
                   relationships_path: Path | str = RELATIONSHIPS_PATH) -> "Catalog":
        return cls(_read_yaml(components_path), _read_yaml(relationships_path))

    def _build_components(self, data: dict[str, Any]) -> None:
        cats = data.get("categories")
        if not isinstance(cats, dict) or not cats:
            raise CatalogError("components.yaml: 'categories' must be a non-empty mapping")
        for key, row in cats.items():
            try:
                self._categories[key] = Category(key, str(row["label"]), str(row["fill_color"]),
                                                 str(row["tint_color"]), str(row["stroke_color"]))
            except (KeyError, TypeError) as e:
                raise CatalogError(f"category {key!r}: needs label/fill_color/tint_color/stroke_color ({e})") from e
            for c in (row["fill_color"], row["tint_color"], row["stroke_color"]):
                if not re.fullmatch(r"#[0-9A-Fa-f]{6}", str(c)):
                    raise CatalogError(f"category {key!r}: bad colour {c!r}")

        raw = data.get("components")
        if not isinstance(raw, list) or not raw:
            raise CatalogError("components.yaml: 'components' must be a non-empty list")
        seen_names: dict[str, str] = {}
        for i, item in enumerate(raw):
            if not isinstance(item, dict):
                raise CatalogError(f"components[{i}]: must be a mapping")
            missing = [k for k in ("name", "shape", "style", "category", "kind") if not item.get(k)]
            if missing:
                raise CatalogError(f"components[{i}] ({item.get('name')!r}): missing {missing}")
            name = str(item["name"])
            if item["style"] not in STYLES:
                raise CatalogError(f"{name}: unknown style {item['style']!r} (expected {STYLES})")
            if item["kind"] not in KINDS:
                raise CatalogError(f"{name}: unknown kind {item['kind']!r} (expected {KINDS})")
            if item["category"] not in self._categories:
                raise CatalogError(f"{name}: unknown category {item['category']!r}")
            syns = item.get("synonyms") or []
            if not isinstance(syns, list):
                raise CatalogError(f"{name}: synonyms must be a list")
            for s in syns:
                if not isinstance(s, str) or s != s.lower() or not normalise(s):
                    raise CatalogError(f"{name}: synonym {s!r} must be a non-empty lowercase string")
            entry = Entry(name=name, shape=str(item["shape"]), style=item["style"], category=item["category"],
                          kind=item["kind"], synonyms=tuple(syns), exact=bool(item.get("exact", False)))
            nkey = normalise(name)
            if not nkey:
                raise CatalogError(f"component name {name!r} normalises to nothing")
            if name in self._by_name or nkey in seen_names:
                raise CatalogError(f"duplicate canonical name: {name!r} (same as {seen_names.get(nkey)!r})")
            seen_names[nkey] = name
            self._by_name[name] = entry
            self._entries.append(entry)

        for entry in self._entries:                       # keys: canonical name + synonyms
            for text in (entry.name, *entry.synonyms):
                key = normalise(text)
                prev = self._index.get(key)
                if prev is not None and prev[0] is not entry:
                    raise CatalogError(f"synonym collision on {key!r}: {text!r} ({entry.name}) "
                                       f"already belongs to {prev[0].name}")
                self._index[key] = (entry, entry.exact)
                self._max_ngram = max(self._max_ngram, len(key.split()))

        fb = data.get("fallbacks")
        if not isinstance(fb, dict):
            raise CatalogError("components.yaml: 'fallbacks' must be a mapping")
        for kind in KINDS:
            target = fb.get(kind)
            if target not in self._by_name:
                raise CatalogError(f"fallbacks.{kind}: {target!r} is not a component name")
            self._fallbacks[kind] = self._by_name[target]

    def _build_relationships(self, data: dict[str, Any]) -> None:
        groups = data.get("groups")
        if not isinstance(groups, dict) or not groups:
            raise CatalogError("relationships.yaml: 'groups' must be a non-empty mapping")
        for group, items in groups.items():
            for item in items or []:
                verb = str(item.get("verb", "")).strip() if isinstance(item, dict) else ""
                if not verb:
                    raise CatalogError(f"relationships group {group!r}: entry without 'verb'")
                if len(verb.split()) > MAX_VERB_WORDS:
                    raise CatalogError(f"verb {verb!r} has more than {MAX_VERB_WORDS} words")
                syns = tuple(item.get("synonyms") or ())
                v = Verb(verb, group, syns)
                for text in (verb, *syns):
                    key = normalise_verb_key(text)
                    if not key:
                        raise CatalogError(f"verb {verb!r}: empty synonym")
                    prev = self._verb_index.get(key)
                    if prev is not None and prev is not v:
                        raise CatalogError(f"verb collision on {key!r}: {text!r} ({verb}) already belongs to {prev.verb}")
                    self._verb_index[key] = v
                self._verbs.append(v)

    # ------------------------------------------------------------------ public API
    def resolve(self, service: Optional[str], name: str, kind: ComponentKind = "aws_service") -> IconRef:
        """Icon for a component. Never raises; unmatched input gives a kind-based generic icon with matched=False.

        Order: `service` (whole text, then longest word-sequence found inside), then `name` (same).
        For kind actor/external/generic the name is only accepted as a whole-text match, so a phrase like
        "Mobile app users" is not mistaken for a service.
        """
        try:
            entry = self._lookup(service, allow_ngram=True)
            if entry is None:
                entry = self._lookup(name, allow_ngram=kind not in ("actor", "external", "generic"))
            if entry is not None:
                return self._icon(entry, matched=True)
        except Exception:                                   # defensive: resolve must never raise
            pass
        return self._icon(self._fallbacks.get(str(kind), self._fallbacks["generic"]), matched=False)

    def canonical_services(self) -> list[str]:
        """Canonical names of the AWS services (generic / non-AWS entries are excluded: they use service=null)."""
        return [e.name for e in self._entries if e.is_aws]

    def relationship_verbs(self) -> list[str]:
        return [v.verb for v in self._verbs]

    def kind_of(self, canonical: str) -> Optional[str]:
        """Default ComponentKind of a canonical (or synonym) name, or None when unknown."""
        e = self._lookup(canonical, allow_ngram=False)
        return e.kind if e else None

    def is_named_in(self, service: Optional[str], name: str, text: str) -> bool:
        """True when the documents name this AWS service: its canonical name or one of its synonyms appears in
        `text` as whole words. Exact-only synonyms (ambiguous words) count only as the full canonical name."""
        entry = self._lookup(service, allow_ngram=True) or self._lookup(name, allow_ngram=True)
        if entry is None:
            return False
        if f" {normalise_verb_key(entry.name)} " in f" {normalise_verb_key(text)} ":   # full name, e.g. "aws glue"
            return True
        if entry.exact:
            return False
        hay = f" {normalise_long(text)} "
        keys = [normalise(entry.name), *(normalise(s) for s in entry.synonyms)]
        return any(k and f" {k} " in hay for k in keys)

    def normalise_verb(self, label: str) -> Optional[str]:
        """Canonical verb for a label or one of its synonyms, or None."""
        v = self._verb_index.get(normalise_verb_key(label))
        return v.verb if v else None

    def entries(self) -> list[Entry]:
        return list(self._entries)

    def categories(self) -> dict[str, Category]:
        return dict(self._categories)

    def extractor_prompt_section(self) -> str:
        """Compact text for the Extractor/Assumer prompt: services with synonyms, verbs, non-AWS guidance."""
        lines = ["AWS services. Set `service` to the canonical name (accepted aliases in parentheses):"]
        for e in self._entries:
            if not e.is_aws:
                continue
            ckey = normalise(e.name)
            shown: list[str] = []
            for s in e.synonyms:
                ns = normalise(s)
                if ns == ckey or len(s) > _PROMPT_MAX_SYNONYM_CHARS:
                    continue
                if f" {ckey} " in f" {ns} ":           # "s3 bucket" adds nothing next to "Amazon S3"
                    continue
                shown.append(s)
                if len(shown) >= _PROMPT_MAX_SYNONYMS:
                    break
            lines.append(f"{e.name} ({', '.join(shown)})" if shown else e.name)

        by_group: dict[str, list[str]] = {}
        for v in self._verbs:
            by_group.setdefault(v.group, []).append(v.verb)
        lines.append("")
        lines.append(f"Relationship labels: at most {MAX_VERB_WORDS} words; prefer these verbs.")
        for group, verbs in by_group.items():
            lines.append(f"{group.replace('_', ' ')}: {'; '.join(verbs)}")

        lines.append("")
        generic = [f"{e.name} ({e.kind})" for e in self._entries if not e.is_aws and e.name != self._fallbacks["aws_service"].name]
        lines.append("Non-AWS items (users, clients, on-prem, SaaS or external APIs, generic databases or apps, devices): "
                     "set service=null and the matching kind. Typical: " + "; ".join(generic) + ".")
        return "\n".join(lines)

    def check_shapes(self, shapes_path: Path | str) -> list[str]:
        """Problems of catalog shapes against aws4-shapes.json (empty list = all valid and in the right list)."""
        import json

        cats = json.loads(Path(shapes_path).read_text(encoding="utf-8"))["categories"]
        sub = set(cats["sub_resources"]["shapes"])
        main = {s for k, v in cats.items() if k not in ("sub_resources", "groups") for s in v["shapes"]}
        problems = []
        for e in self._entries:
            if e.style == "resource_icon" and e.shape not in main:
                problems.append(f"{e.name}: {e.shape!r} is not a main-service shape")
            elif e.style in ("sub_resource", "general") and e.shape not in sub:
                problems.append(f"{e.name}: {e.shape!r} is not in sub_resources")
        return problems

    # ------------------------------------------------------------------ internals
    def _icon(self, entry: Entry, matched: bool) -> IconRef:
        c = self._categories[entry.category]
        return IconRef(shape=entry.shape, style=entry.style, category=entry.category, fill_color=c.fill_color,
                       tint_color=c.tint_color, stroke_color=c.stroke_color, category_label=c.label, matched=matched)

    def _lookup(self, text: Optional[str], allow_ngram: bool) -> Optional[Entry]:
        norm = normalise(text)
        if not norm:
            return None
        hit = self._index.get(norm)
        if hit is not None:
            return hit[0]
        if not allow_ngram:
            return None
        tokens = norm.split()
        best: Optional[tuple[tuple[bool, int, int], Entry]] = None
        for n in range(min(len(tokens), self._max_ngram), 0, -1):
            for i in range(len(tokens) - n + 1):
                hit = self._index.get(" ".join(tokens[i:i + n]))
                if hit is None or hit[1]:                   # exact-only keys never match inside a phrase
                    continue
                rank = (hit[0].is_aws, n, -i)               # AWS before generic, then longer, then leftmost
                if best is None or rank > best[0]:
                    best = (rank, hit[0])
        return best[1] if best else None


def normalise_long(text: Any) -> str:
    """`normalise` without the input cap and the trailing-word rule, for searching whole documents."""
    t = str(text or "").lower().replace("&", " and ")
    return " ".join(x for x in re.sub(r"[^a-z0-9]+", " ", t).split() if x not in _DROP_TOKENS)


def normalise_verb_key(text: Any) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).split())


def _read_yaml(path: Path | str) -> dict[str, Any]:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise CatalogError(f"cannot read {path}: {e}") from e
    if not isinstance(data, dict):
        raise CatalogError(f"{path}: top level must be a mapping")
    return data


@lru_cache(maxsize=None)
def _load(components_path: str, relationships_path: str) -> Catalog:
    return Catalog.from_files(components_path, relationships_path)


def load_catalog(settings=None) -> Catalog:
    """The packaged catalog, loaded and validated once (cached). `settings` is accepted for API parity."""
    return _load(str(COMPONENTS_PATH), str(RELATIONSHIPS_PATH))
