"""Microsoft Dynamics 365 (Dataverse Web API) client for the Funding check flow, with a demo store fallback.

Live mode (DYNAMICS_URL set, e.g. https://contoso.crm4.dynamics.com):
  * OAuth 2.0 client credentials for an Entra app registration that is a Dataverse *application user*
    (scope "<DYNAMICS_URL>/.default"). The client secret is read from Secrets Manager (DYNAMICS_SECRET_ARN).
  * An opportunity is a standard `opportunity` row (name, description, estimatedvalue, customer account found or
    created by name) plus one `ingr_` column per form question (opportunity_form.COLUMNS; choice questions are
    choice columns). DYNAMICS_FORM_STORAGE=note instead stores the answers as a JSON note (annotation) with the
    subject ANSWERS_SUBJECT, for environments without the columns. Reading always accepts both, columns first.
    Funding decisions are written back as a note.

Demo mode (DYNAMICS_URL empty): the same records are kept in DynamoDB (`DYNOPP#<id>`), so the whole flow can be
used before Dynamics access is granted. Every record says which mode it came from (`source`).

Standard library only, so the API Lambda (layer) and the AgentCore agent share this file.
"""

import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import opportunity_form
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

logger = logging.getLogger("dynamics")

API = "/api/data/v9.2"
ANSWERS_SUBJECT = "Ingram intake form answers (JSON)"
DECISION_SUBJECT_PREFIX = "AWS funding check"
GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
FORMATTED = "@OData.Community.Display.V1.FormattedValue"
MAX_NOTE_CHARS = 90_000      # annotation notetext holds 100,000 characters
TIMEOUT = 12

_token_lock = threading.Lock()
_token: dict[str, Any] = {}


class DynamicsError(Exception):
    """Dynamics could not be reached or refused the request; the message is safe to show the user."""

    def __init__(self, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.status = status


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def mode() -> str:
    return "dynamics" if os.environ.get("DYNAMICS_URL", "").strip() else "demo"


def storage() -> str:
    """columns (one ingr_ column per question, the default) | note (answers as a JSON note)."""
    return "note" if os.environ.get("DYNAMICS_FORM_STORAGE", "columns").strip().lower() == "note" else "columns"


def is_mock() -> bool:
    """True when DYNAMICS_URL points at the mock Dataverse (mock-dataverse/, dummy data). The client code path is
    the real one; only the label in the UI and the missing 'Open in Dynamics' link differ."""
    return mode() == "dynamics" and os.environ.get("DYNAMICS_MOCK", "").lower() == "true"


def flavour() -> str:
    """demo (DynamoDB stand-in) | mock (Dataverse-compatible mock) | dynamics (real Dynamics 365)."""
    return "mock" if is_mock() else mode()


def is_guid(value: str) -> bool:
    return bool(GUID.match(value or ""))


def _odata_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


# ---------------------------------------------------------------- live: Dataverse Web API

def _base() -> str:
    url = os.environ["DYNAMICS_URL"].strip().rstrip("/")
    if not url.startswith("https://"):
        raise DynamicsError("DYNAMICS_URL must start with https://", 500)
    return url


def _client_secret() -> str:
    arn = os.environ.get("DYNAMICS_SECRET_ARN", "")
    if not arn:
        raise DynamicsError("Dynamics is configured without DYNAMICS_SECRET_ARN.", 500)
    import boto3   # available in Lambda and in the agent; imported here so demo mode never needs it

    value = boto3.client("secretsmanager").get_secret_value(SecretId=arn)["SecretString"]
    try:
        parsed = json.loads(value)
        return parsed["client_secret"] if isinstance(parsed, dict) else str(parsed)
    except ValueError:
        return value.strip()


def _access_token() -> str:
    with _token_lock:
        if _token.get("value") and _token["expires"] > time.time() + 120:
            return _token["value"]
        tenant = os.environ.get("DYNAMICS_TENANT_ID", "")
        data = urllib.parse.urlencode({
            "client_id": os.environ["DYNAMICS_CLIENT_ID"], "client_secret": _client_secret(),
            "grant_type": "client_credentials", "scope": f"{_base()}/.default"}).encode()
        # DYNAMICS_TOKEN_URL: the mock Dataverse issues its own tokens; real Dynamics uses Microsoft Entra ID.
        token_url = os.environ.get("DYNAMICS_TOKEN_URL") or f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
        req = urllib.request.Request(token_url, data=data,
                                     headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                body = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            detail = _error_text(exc)
            logger.warning("Dynamics token request failed: %s %s", exc.code, detail)
            raise DynamicsError(f"Could not sign in to Dynamics ({detail or exc.code}).") from None
        except OSError as exc:
            raise DynamicsError(f"Could not reach Microsoft sign-in: {exc}") from None
        _token.update(value=body["access_token"], expires=time.time() + int(body.get("expires_in", 3600)))
        return _token["value"]


def _error_text(exc: urllib.error.HTTPError) -> str:
    try:
        body = json.loads(exc.read())
    except (ValueError, OSError):
        return ""
    err = body.get("error")
    if isinstance(err, dict):
        return str(err.get("message", ""))[:300]
    return str(body.get("error_description") or err or "")[:300]


def _request(method: str, path: str, body: dict[str, Any] | None = None, prefer: str = "",
             extra_headers: dict[str, str] | None = None) -> tuple[dict[str, Any], dict]:
    headers = {"Authorization": f"Bearer {_access_token()}", "Accept": "application/json",
               "OData-MaxVersion": "4.0", "OData-Version": "4.0"}
    if body is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"
    if prefer:
        headers["Prefer"] = prefer
    headers.update(extra_headers or {})
    req = urllib.request.Request(f"{_base()}{API}/{_encode(path)}", method=method, headers=headers,
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read()
            return (json.loads(raw) if raw else {}), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        detail = _error_text(exc)
        logger.warning("Dataverse %s %s -> %s %s", method, path.split("?")[0], exc.code, detail)
        if exc.code == 404:
            raise DynamicsError("Opportunity not found in Dynamics.", 404) from None
        if exc.code in (401, 403):
            raise DynamicsError("Dynamics refused access: check the application user and its security role.", 502) from None
        raise DynamicsError(f"Dynamics error {exc.code}: {detail or 'request failed'}") from None
    except OSError as exc:
        raise DynamicsError(f"Could not reach Dynamics: {exc}") from None


def _encode(path: str) -> str:
    """Percent-encode each OData query option value ("name eq 'A & B'", "createdon desc"), keeping $ names readable.
    Values must be split on the option separators first, so '&' or '=' inside a value cannot break the query."""
    resource, _, query = path.partition("?")
    if not query:
        return resource
    options = []
    for part in re.split(r"&(?=\$[a-z]+=)", query):   # every option starts with $name=
        name, _, value = part.partition("=")
        options.append(name + "=" + urllib.parse.quote(value, safe="(),'"))
    return resource + "?" + "&".join(options)


def _id_from_headers(headers: dict) -> str:
    entity = next((v for k, v in headers.items() if k.lower() == "odata-entityid"), "")
    found = re.search(r"\(([0-9a-fA-F-]{36})\)", entity)
    return found.group(1) if found else ""


def _find_or_create_account(name: str) -> str:
    page, _ = _request("GET", f"accounts?$select=accountid&$filter=name eq {_odata_str(name)}&$top=1")
    if page.get("value"):
        return page["value"][0]["accountid"]
    _, headers = _request("POST", "accounts", {"name": name[:160]})
    return _id_from_headers(headers)


def _note(opportunity_id: str, subject: str, text: str) -> None:
    _request("POST", "annotations", {"subject": subject[:500], "notetext": text[:MAX_NOTE_CHARS],
                                     "objectid_opportunity@odata.bind": f"/opportunities({opportunity_id})"})


def _record_url(opportunity_id: str) -> str | None:
    if is_mock():   # the mock has no Dynamics UI to link to
        return None
    return f"{_base()}/main.aspx?etn=opportunity&pagetype=entityrecord&id={opportunity_id}"


def _live_create(fields: dict[str, Any], answers: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {"name": fields["name"][:300], "description": fields.get("description", "")[:2000]}
    if fields.get("estimated_value") is not None:
        body["estimatedvalue"] = float(fields["estimated_value"])
    if fields.get("close_date"):
        body["estimatedclosedate"] = fields["close_date"]
    if fields.get("customer"):
        account_id = _find_or_create_account(fields["customer"])
        if account_id:
            body["customerid_account@odata.bind"] = f"/accounts({account_id})"
    if storage() == "columns":
        body.update(opportunity_form.to_columns(answers))
    try:
        _, headers = _request("POST", "opportunities", body)
    except DynamicsError as exc:
        if storage() == "columns" and "propert" in str(exc).lower():
            raise DynamicsError("Dynamics does not have the intake form columns (ingr_...). Create them with "
                                "scripts/create_dynamics_columns.py, or set DYNAMICS_FORM_STORAGE=note.") from None
        raise
    opp_id = _id_from_headers(headers)
    if not opp_id:
        raise DynamicsError("Dynamics created the opportunity but did not return its ID.")
    if storage() == "note":
        _note(opp_id, ANSWERS_SUBJECT, json.dumps(answers, ensure_ascii=False))
    return {"id": opp_id, "url": _record_url(opp_id)}


def _live_get(opportunity_id: str) -> dict[str, Any]:
    prefer = 'odata.include-annotations="OData.Community.Display.V1.FormattedValue"'
    select = "name,description,estimatedvalue,estimatedclosedate,createdon,statecode,_customerid_value"
    if storage() == "columns":
        select += "," + ",".join(opportunity_form.answer_columns())
    opp, _ = _request("GET", f"opportunities({opportunity_id})?$select={select}", prefer=prefer)
    notes, _ = _request("GET", "annotations?$select=subject,notetext,createdon&$filter=_objectid_value eq "
                               f"{opportunity_id}&$orderby=createdon desc&$top=20")
    note_answers: dict[str, Any] = {}
    for n in notes.get("value", []):
        if n.get("subject") == ANSWERS_SUBJECT:
            try:
                note_answers = json.loads(n.get("notetext") or "{}")
            except ValueError:
                logger.warning("answers note of %s is not JSON", opportunity_id)
            break
    columns = opportunity_form.from_columns(opp) if storage() == "columns" else {}
    # Columns win; an older record's answers note fills what the columns don't have.
    answers = {**(note_answers if isinstance(note_answers, dict) else {}), **columns}
    other_notes = [{"subject": n.get("subject") or "", "text": (n.get("notetext") or "")[:4000],
                    "created_on": n.get("createdon")}
                   for n in notes.get("value", [])
                   if n.get("subject") != ANSWERS_SUBJECT and not (n.get("subject") or "").startswith(DECISION_SUBJECT_PREFIX)]
    return {"id": opportunity_id, "source": "dynamics", "url": _record_url(opportunity_id),
            "name": opp.get("name") or "", "description": opp.get("description") or "",
            "customer": opp.get(f"_customerid_value{FORMATTED}") or "",
            "estimated_value": opp.get("estimatedvalue"), "created_on": opp.get("createdon"),
            "state": opp.get(f"statecode{FORMATTED}") or "", "close_date": opp.get("estimatedclosedate"),
            "answers": answers, "answers_from": "columns" if columns else "note" if note_answers else None,
            "notes": other_notes[:5]}


STATES = {"open": 0, "won": 1, "lost": 2}


def _live_search(text: str, top: int, state: str) -> list[dict[str, Any]]:
    prefer = 'odata.include-annotations="OData.Community.Display.V1.FormattedValue"'
    filters = []
    if text:
        filters.append(f"contains(name,{_odata_str(text)})")
    if state in STATES:
        filters.append(f"statecode eq {STATES[state]}")
    extra = ",ingr_requesttype,ingr_awsopportunitytype,ingr_fundworkload" if storage() == "columns" else ""
    query = ("opportunities?$select=name,description,estimatedvalue,estimatedclosedate,createdon,modifiedon,statecode,"
             f"_customerid_value{extra}&$orderby=createdon desc&$top={top}")
    if filters:
        query += "&$filter=" + " and ".join(filters)
    page, _ = _request("GET", query, prefer=prefer)
    return [{"id": o["opportunityid"], "name": o.get("name") or "", "customer": o.get(f"_customerid_value{FORMATTED}") or "",
             "description": (o.get("description") or "")[:300], "estimated_value": o.get("estimatedvalue"),
             "close_date": o.get("estimatedclosedate"), "created_on": o.get("createdon"),
             "state": o.get(f"statecode{FORMATTED}") or "", "source": "dynamics",
             "request_type": opportunity_form.from_columns(o).get("request_type", ""),
             "workload": (lambda a: a.get("fund_workload") or a.get("aws_opportunity_type") or "")(
                 opportunity_form.from_columns(o)),
             "url": _record_url(o["opportunityid"])} for o in page.get("value", [])]


# ---------------------------------------------------------------- demo: DynamoDB stand-in with the same shape

def _table():
    import boto3

    return boto3.resource("dynamodb").Table(os.environ["RUNS_TABLE"])


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _dynamo(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str), parse_float=Decimal)


DEMO_OWNER = "DEMO#DYNAMICS"   # index key (owner-created) that lists every demo opportunity, newest first


def demo_put(opp_id: str, fields: dict[str, Any], answers: dict[str, Any], created_on: str | None = None) -> None:
    created_on = created_on or _now()
    _table().put_item(Item=_dynamo({
        "pk": f"DYNOPP#{opp_id}", "sk": "META", "opportunity_id": opp_id, "name": fields["name"][:300],
        "description": fields.get("description", "")[:2000], "customer": fields.get("customer", ""),
        "estimated_value": fields.get("estimated_value"), "state": fields.get("state", "Open"),
        "close_date": fields.get("close_date"), "created_on": created_on,
        "owner": DEMO_OWNER, "created_at": created_on,
        "answers_json": json.dumps(answers, ensure_ascii=False), "decisions": []}))


def _demo_create(fields: dict[str, Any], answers: dict[str, Any]) -> dict[str, Any]:
    opp_id = str(uuid.uuid4())
    demo_put(opp_id, fields, answers)
    return {"id": opp_id, "url": None}


def _demo_search(text: str, top: int, state: str) -> list[dict[str, Any]]:
    from boto3.dynamodb.conditions import Key

    table = _table()
    page = table.query(IndexName="owner-created", KeyConditionExpression=Key("owner").eq(DEMO_OWNER),
                       ScanIndexForward=False, Limit=100)
    keys = [{"pk": i["pk"], "sk": i["sk"]} for i in page["Items"]]
    if not keys:
        return []
    got = table.meta.client.batch_get_item(RequestItems={table.name: {"Keys": keys}})["Responses"].get(table.name, [])
    items = sorted((_plain(i) for i in got), key=lambda i: i.get("created_on") or "", reverse=True)
    needle = text.lower()
    out = []
    for i in items:
        if needle and needle not in (i.get("name") or "").lower():
            continue
        if state in STATES and (i.get("state") or "Open").lower() != state:
            continue
        out.append({"id": i["opportunity_id"], "name": i.get("name", ""), "customer": i.get("customer", ""),
                    "description": (i.get("description") or "")[:300], "estimated_value": i.get("estimated_value"),
                    "close_date": i.get("close_date"), "created_on": i.get("created_on"),
                    "state": i.get("state") or "Open", "source": "demo", "url": None,
                    "request_type": json.loads(i.get("answers_json") or "{}").get("request_type", "")})
    return out[:top]


def _demo_get(opportunity_id: str) -> dict[str, Any]:
    item = _table().get_item(Key={"pk": f"DYNOPP#{opportunity_id}", "sk": "META"}).get("Item")
    if not item:
        raise DynamicsError("Opportunity not found in the demo store.", 404)
    item = _plain(item)
    return {"id": opportunity_id, "source": "demo", "url": None, "name": item.get("name", ""),
            "description": item.get("description", ""), "customer": item.get("customer", ""),
            "estimated_value": item.get("estimated_value"), "created_on": item.get("created_on"),
            "state": item.get("state") or "Open", "close_date": item.get("close_date"),
            "answers": json.loads(item.get("answers_json") or "{}"), "notes": []}


# ---------------------------------------------------------------- public API

def create_opportunity(fields: dict[str, Any], answers: dict[str, Any]) -> dict[str, Any]:
    """fields {name, customer, description, estimated_value} -> {id, url, source}."""
    result = _live_create(fields, answers) if mode() == "dynamics" else _demo_create(fields, answers)
    return {**result, "source": mode()}


def get_opportunity(opportunity_id: str) -> dict[str, Any]:
    if not is_guid(opportunity_id):
        raise DynamicsError("Not a Dynamics opportunity ID (GUID).", 400)
    return _live_get(opportunity_id) if mode() == "dynamics" else _demo_get(opportunity_id)


def search_opportunities(text: str = "", top: int = 50, state: str = "all") -> list[dict[str, Any]]:
    """Recent opportunities, newest first: from Dynamics, or from the demo store. state: open | won | lost | all."""
    text, top = text.strip()[:100], max(1, min(top, 100))
    return _live_search(text, top, state) if mode() == "dynamics" else _demo_search(text, top, state)


def delete_opportunity(opportunity_id: str) -> None:
    """Delete an opportunity and its notes (used by scripts/seed_dynamics.py --reset)."""
    notes, _ = _request("GET", f"annotations?$select=annotationid&$filter=_objectid_value eq {opportunity_id}&$top=500")
    for n in notes.get("value", []):
        _request("DELETE", f"annotations({n['annotationid']})")
    _request("DELETE", f"opportunities({opportunity_id})")


def write_decision(opportunity_id: str, subject: str, text: str) -> None:
    """Attach the funding decision to the opportunity (a note in Dynamics; a list entry in demo mode)."""
    if mode() == "dynamics":
        _note(opportunity_id, f"{DECISION_SUBJECT_PREFIX}: {subject}", text)
        return
    _table().update_item(
        Key={"pk": f"DYNOPP#{opportunity_id}", "sk": "META"},
        UpdateExpression="SET decisions = list_append(if_not_exists(decisions, :e), :d)",
        ExpressionAttributeValues={":e": [], ":d": [{"subject": subject[:500], "text": text[:MAX_NOTE_CHARS], "at": _now()}]})
