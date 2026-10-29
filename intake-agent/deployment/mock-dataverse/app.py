"""Mock Microsoft Dynamics 365 (Dataverse Web API v9.2) on AWS Lambda, backed by dummy DynamoDB tables.

Lets the funding agent fetch opportunities "from Dynamics" before a real environment exists. The agent and the API
use their normal Dataverse client (shared/python/dynamics.py): OAuth 2.0 client credentials, then OData calls.
Point DYNAMICS_URL at a real environment later and nothing else changes.

Dummy tables (DynamoDB, one per Dataverse table; keys are the Dataverse primary keys):
    ingram-mock-dynamics-accounts       accountid      name, createdon
    ingram-mock-dynamics-opportunities  opportunityid  name, description, estimatedvalue, estimatedclosedate,
                                                       statecode, _customerid_value, createdon, modifiedon,
                                                       + one ingr_ column per intake form question (118, typed as
                                                       in shared/python/opportunity_form.COLUMNS)
    ingram-mock-dynamics-annotations    annotationid   subject, notetext, _objectid_value, objecttypecode, createdon

Endpoints (the subset of the Web API the client uses, same JSON and error shapes as Dataverse):
    POST /oauth2/v2.0/token                    client_credentials -> {access_token, expires_in}  (HMAC-signed token)
    GET  /api/data/v9.2/WhoAmI
    GET  /api/data/v9.2/{accounts|opportunities|annotations}?$select&$filter&$orderby&$top
    GET  /api/data/v9.2/opportunities(<id>)?$select
    POST /api/data/v9.2/{accounts|opportunities|annotations}   -> 204 + OData-EntityId
    DELETE /api/data/v9.2/{accounts|opportunities|annotations}(<id>)  -> 204
$filter supports: `field eq 'text'|number|guid`, `contains(field,'text')`, joined with `and`. Not a general OData
engine; anything else returns a Dataverse-style 400 so a mismatch is visible rather than silently wrong.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import time
import urllib.parse
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr

import opportunity_form   # shared layer: the ingr_ column catalogue

logger = logging.getLogger()
logger.setLevel(logging.INFO)

API = "/api/data/v9.2"
FORMATTED = "@OData.Community.Display.V1.FormattedValue"
TOKEN_SECONDS = 3600
STATE = {0: "Open", 1: "Won", 2: "Lost"}
TABLES = {  # entity set -> (table env var, primary key)
    "accounts": ("ACCOUNTS_TABLE", "accountid"),
    "opportunities": ("OPPORTUNITIES_TABLE", "opportunityid"),
    "annotations": ("ANNOTATIONS_TABLE", "annotationid"),
}
WRITABLE = {
    "accounts": {"name"},
    "opportunities": {"name", "description", "estimatedvalue", "estimatedclosedate", "statecode"},
    "annotations": {"subject", "notetext"},
}
GUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
MAX_TOP = 500
FORM_COLUMNS = opportunity_form.BY_COLUMN
WRITABLE["opportunities"] |= set(FORM_COLUMNS)
_db = boto3.resource("dynamodb")
_secret_cache: dict[str, str] = {}


class ODataError(Exception):
    def __init__(self, status: int, message: str, code: str = "0x80040217") -> None:
        super().__init__(message)
        self.status, self.code = status, code


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _plain(v: Any) -> Any:
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_plain(x) for x in v]
    return v


def _table(entity: str):
    return _db.Table(os.environ[TABLES[entity][0]])


def _json(status: int, body: Any = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
    out = {"statusCode": status, "headers": {"Content-Type": "application/json; odata.metadata=minimal",
                                             "OData-Version": "4.0", **(headers or {})}}
    if body is not None:
        out["body"] = json.dumps(body, default=str)
    return out


def _odata_error(exc: ODataError) -> dict[str, Any]:
    return _json(exc.status, {"error": {"code": exc.code, "message": str(exc)}})


# ---------------------------------------------------------------- auth: client credentials -> signed token

def _client_secret() -> str:
    if "v" not in _secret_cache:
        raw = boto3.client("secretsmanager").get_secret_value(SecretId=os.environ["CLIENT_SECRET_ARN"])["SecretString"]
        try:
            parsed = json.loads(raw)
            raw = parsed.get("client_secret", raw) if isinstance(parsed, dict) else raw
        except ValueError:
            pass
        _secret_cache["v"] = raw.strip()
    return _secret_cache["v"]


def _sign(payload: str) -> str:
    return hmac.new(_client_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()


def token(event: dict[str, Any]) -> dict[str, Any]:
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode()
    form = dict(urllib.parse.parse_qsl(body))
    if form.get("grant_type") != "client_credentials":
        return _json(400, {"error": "unsupported_grant_type", "error_description": "AADSTS70003: only client_credentials"})
    ok_id = hmac.compare_digest(form.get("client_id", ""), os.environ["CLIENT_ID"])
    ok_secret = hmac.compare_digest(form.get("client_secret", ""), _client_secret())
    if not (ok_id and ok_secret):
        logger.warning("token refused for client %s", form.get("client_id", "")[:40])
        return _json(401, {"error": "invalid_client",
                           "error_description": "AADSTS7000215: Invalid client secret provided (mock Dataverse)."})
    exp = int(time.time()) + TOKEN_SECONDS
    payload = base64.urlsafe_b64encode(json.dumps({"sub": form["client_id"], "exp": exp}).encode()).decode()
    return _json(200, {"token_type": "Bearer", "expires_in": TOKEN_SECONDS, "access_token": f"{payload}.{_sign(payload)}"})


def _authorise(event: dict[str, Any]) -> None:
    header = (event.get("headers") or {}).get("authorization", "")
    tok = header[7:].strip() if header.lower().startswith("bearer ") else ""
    payload, _, sig = tok.partition(".")
    if not payload or not hmac.compare_digest(sig, _sign(payload)):
        raise ODataError(401, "The user is not authenticated (mock Dataverse: missing or invalid token).", "0x80040220")
    try:
        exp = json.loads(base64.urlsafe_b64decode(payload.encode()))["exp"]
    except (ValueError, KeyError):
        raise ODataError(401, "Invalid token.", "0x80040220") from None
    if exp < time.time():
        raise ODataError(401, "The access token has expired.", "0x80040220")


# ---------------------------------------------------------------- OData query subset

_COND = re.compile(rf"^\s*(?:contains\((\w+)\s*,\s*'((?:[^']|'')*)'\)|(\w+)\s+eq\s+('(?:[^']|'')*'|{GUID}|-?\d+(?:\.\d+)?))\s*$")


def parse_filter(text: str) -> list[tuple[str, str, Any]]:
    """`a eq 'x' and contains(b,'y') and c eq 0` -> [(field, op, value)]. Strings may contain ' and ' only inside quotes."""
    parts, buf, quoted = [], "", False
    i = 0
    while i < len(text):   # split on top-level " and "
        ch = text[i]
        if ch == "'":
            quoted = not quoted
        if not quoted and text[i:i + 5].lower() == " and ":
            parts.append(buf)
            buf, i = "", i + 5
            continue
        buf += ch
        i += 1
    parts.append(buf)
    out = []
    for p in parts:
        m = _COND.match(p)
        if not m:
            raise ODataError(400, f"Mock Dataverse does not support this filter: {p.strip()[:120]}", "0x80060888")
        if m.group(1):
            out.append((m.group(1), "contains", m.group(2).replace("''", "'")))
        else:
            raw = m.group(4)
            value: Any = raw[1:-1].replace("''", "'") if raw.startswith("'") else (
                raw.lower() if re.fullmatch(GUID, raw) else float(raw) if "." in raw else int(raw))
            out.append((m.group(3), "eq", value))
    return out


def _matches(item: dict[str, Any], conds: list[tuple[str, str, Any]]) -> bool:
    for field, op, value in conds:
        have = item.get(field)
        if op == "contains":
            if value.lower() not in str(have or "").lower():
                return False
        elif isinstance(value, str):
            if str(have or "").lower() != value.lower():
                return False
        elif have is None or float(have) != float(value):
            return False
    return True


def _decorate(entity: str, item: dict[str, Any]) -> dict[str, Any]:
    """Add the formatted values Dataverse returns with Prefer: odata.include-annotations."""
    out = dict(item)
    if entity == "opportunities":
        if out.get("_customerid_value"):
            acc = _table("accounts").get_item(Key={"accountid": out["_customerid_value"]}).get("Item")
            if acc:
                out[f"_customerid_value{FORMATTED}"] = acc.get("name", "")
        if out.get("statecode") is not None:
            out[f"statecode{FORMATTED}"] = STATE.get(int(out["statecode"]), "")
        if out.get("estimatedvalue") is not None:
            out[f"estimatedvalue{FORMATTED}"] = f"${float(out['estimatedvalue']):,.2f}"
        for name, c in FORM_COLUMNS.items():   # choice columns: the option label, as Dataverse returns it
            if c["column_type"] == "Choice" and out.get(name) is not None:
                i = int(out[name]) - opportunity_form.CHOICE_BASE
                if 0 <= i < len(c["options"]):
                    out[f"{name}{FORMATTED}"] = c["options"][i]
    return out


def _project(entity: str, item: dict[str, Any], select: list[str]) -> dict[str, Any]:
    key = TABLES[entity][1]
    if not select:
        return item
    keep = set(select) | {key}
    return {k: v for k, v in item.items() if k in keep or (k.endswith(FORMATTED) and k[: -len(FORMATTED)] in keep)}


def _options(event: dict[str, Any]) -> dict[str, str]:
    raw = event.get("rawQueryString") or ""
    return {k: v for k, v in urllib.parse.parse_qsl(raw, keep_blank_values=True)}


def _formatted_wanted(event: dict[str, Any]) -> bool:
    return "FormattedValue" in (event.get("headers") or {}).get("prefer", "")


def list_entity(entity: str, event: dict[str, Any]) -> dict[str, Any]:
    opts = _options(event)
    conds = parse_filter(opts["$filter"]) if opts.get("$filter") else []
    try:
        top = min(int(opts.get("$top", "50")), MAX_TOP)
    except ValueError:
        raise ODataError(400, "$top must be a number", "0x80060888") from None
    table, items, kwargs = _table(entity), [], {}
    # Notes are always read per opportunity: use a filter expression so the scan reads less.
    eq_obj = next((v for f, op, v in conds if f == "_objectid_value" and op == "eq"), None)
    if eq_obj:
        kwargs["FilterExpression"] = Attr("_objectid_value").eq(eq_obj)
    while True:   # dummy tables are small; a scan is fine (real Dataverse indexes these columns)
        page = table.scan(**kwargs)
        items += [_plain(i) for i in page["Items"]]
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    items = [i for i in items if _matches(i, conds)]
    order = (opts.get("$orderby") or "").strip()
    if order:
        field, _, direction = order.partition(" ")
        items.sort(key=lambda i: (i.get(field) is None, i.get(field) or ""), reverse=direction.strip().lower() == "desc")
    select = [s.strip() for s in opts.get("$select", "").split(",") if s.strip()]
    rows = [_project(entity, _decorate(entity, i) if _formatted_wanted(event) else i, select) for i in items[:top]]
    return _json(200, {"@odata.context": f"https://mock/api/data/v9.2/$metadata#{entity}", "value": rows})


def get_entity(entity: str, record_id: str, event: dict[str, Any]) -> dict[str, Any]:
    key = TABLES[entity][1]
    item = _table(entity).get_item(Key={key: record_id.lower()}).get("Item")
    if not item:
        raise ODataError(404, f"{entity[:-1]} With Id = {record_id} Does Not Exist", "0x80040217")
    select = [s.strip() for s in _options(event).get("$select", "").split(",") if s.strip()]
    item = _plain(item)
    return _json(200, _project(entity, _decorate(entity, item) if _formatted_wanted(event) else item, select))


_BIND = re.compile(rf"^/?(accounts|opportunities)\(({GUID})\)$")


def _check_column(c: dict[str, Any], name: str, v: Any) -> Any:
    """Reject values a real Dataverse column of this type would reject."""
    kind = c["column_type"]
    bad = lambda what: ODataError(400, f"Cannot convert the literal '{str(v)[:60]}' to the expected type "  # noqa: E731
                                       f"'{what}' (column {name}).", "0x80048d19")
    if kind == "Choice":
        if not isinstance(v, int) or not 0 <= v - opportunity_form.CHOICE_BASE < len(c["options"]):
            raise ODataError(400, f"{v} is not a valid option for column {name}.", "0x8004431a")
        return v
    if kind == "WholeNumber":
        if not isinstance(v, int) or isinstance(v, bool):
            raise bad("Edm.Int32")
        return v
    if kind == "Decimal":
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            raise bad("Edm.Decimal")
        return v
    if not isinstance(v, str):
        raise bad("Edm.String")
    limit = 8000 if kind == "MultilineText" else 300
    if len(v) > limit:
        raise ODataError(400, f"A validation error occurred. The length of the '{name}' attribute exceeded the maximum "
                              f"allowed length of '{limit}'.", "0x80044331")
    if kind == "DateOnly" and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        raise bad("Edm.Date")
    return v


def delete_entity(entity: str, record_id: str) -> dict[str, Any]:
    key = TABLES[entity][1]
    if not _table(entity).get_item(Key={key: record_id.lower()}).get("Item"):
        raise ODataError(404, f"{entity[:-1]} With Id = {record_id} Does Not Exist", "0x80040217")
    _table(entity).delete_item(Key={key: record_id.lower()})
    return _json(204)


def create_entity(entity: str, event: dict[str, Any]) -> dict[str, Any]:
    try:
        body = json.loads(event.get("body") or "{}")
    except ValueError:
        raise ODataError(400, "Request body is not valid JSON.", "0x80048d19") from None
    if not isinstance(body, dict):
        raise ODataError(400, "Request body must be an object.", "0x80048d19")
    key = TABLES[entity][1]
    record_id, now = str(uuid.uuid4()), _now()
    item: dict[str, Any] = {key: record_id, "createdon": now, "modifiedon": now}
    for k, v in body.items():
        if entity == "opportunities" and k in FORM_COLUMNS:
            if v is not None:
                item[k] = _check_column(FORM_COLUMNS[k], k, v)
        elif k in WRITABLE[entity]:
            item[k] = v
        elif entity == "opportunities" and k == "customerid_account@odata.bind":
            m = _BIND.match(str(v))
            if not m or m.group(1) != "accounts":
                raise ODataError(400, "customerid_account@odata.bind must be /accounts(<id>)", "0x80048d19")
            if not _table("accounts").get_item(Key={"accountid": m.group(2).lower()}).get("Item"):
                raise ODataError(404, f"account With Id = {m.group(2)} Does Not Exist")
            item["_customerid_value"] = m.group(2).lower()
        elif entity == "annotations" and k == "objectid_opportunity@odata.bind":
            m = _BIND.match(str(v))
            if not m or m.group(1) != "opportunities":
                raise ODataError(400, "objectid_opportunity@odata.bind must be /opportunities(<id>)", "0x80048d19")
            if not _table("opportunities").get_item(Key={"opportunityid": m.group(2).lower()}).get("Item"):
                raise ODataError(404, f"opportunity With Id = {m.group(2)} Does Not Exist")
            item["_objectid_value"], item["objecttypecode"] = m.group(2).lower(), "opportunity"
        else:
            raise ODataError(400, f"Invalid property '{k}' was found in entity 'Microsoft.Dynamics.CRM.{entity[:-1]}' "
                                  "(mock Dataverse: property not writable).", "0x80048d19")
    if entity in ("accounts", "opportunities") and not str(item.get("name") or "").strip():
        raise ODataError(400, "name is required", "0x80040200")
    if entity == "opportunities":
        item.setdefault("statecode", 0)
    if entity == "annotations" and len(str(item.get("notetext") or "")) > 100_000:
        raise ODataError(400, "notetext is longer than 100,000 characters", "0x80044331")
    _table(entity).put_item(Item=json.loads(json.dumps(item), parse_float=Decimal))
    base = f"https://{(event.get('headers') or {}).get('host', 'mock')}{API}"
    return _json(204, headers={"OData-EntityId": f"{base}/{entity}({record_id})"})


# ---------------------------------------------------------------- routing

_RECORD = re.compile(rf"^{API}/(accounts|opportunities|annotations)\(({GUID})\)$")
_SET = re.compile(rf"^{API}/(accounts|opportunities|annotations)$")


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    path = event.get("rawPath", "")
    try:
        if path.endswith("/oauth2/v2.0/token") and method == "POST":
            return token(event)
        _authorise(event)
        if path == f"{API}/WhoAmI" and method == "GET":
            return _json(200, {"UserId": "00000000-0000-0000-0000-00000000a11c", "BusinessUnitId":
                               "00000000-0000-0000-0000-0000000000b0", "OrganizationId": "00000000-0000-0000-0000-0000000000c0"})
        if (m := _RECORD.match(path)) and method == "GET":
            return get_entity(m.group(1), m.group(2), event)
        if (m := _RECORD.match(path)) and method == "DELETE":
            return delete_entity(m.group(1), m.group(2))
        if (m := _SET.match(path)):
            if method == "GET":
                return list_entity(m.group(1), event)
            if method == "POST":
                return create_entity(m.group(1), event)
        raise ODataError(404, f"Resource not found for the segment '{path.rsplit('/', 1)[-1][:80]}'.", "0x80060888")
    except ODataError as exc:
        return _odata_error(exc)
    except Exception:
        logger.exception("mock dataverse error on %s %s", method, path)
        return _json(500, {"error": {"code": "0x80040216", "message": "An unexpected error occurred."}})
