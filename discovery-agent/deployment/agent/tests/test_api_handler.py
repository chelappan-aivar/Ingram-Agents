"""Offline unit tests for the discovery API Lambda (deployment/api/handler.py). No AWS: S3, DynamoDB and the
AgentCore client are replaced with in-memory fakes.

Run (from deployment/agent):  python -m unittest tests.test_api_handler -v
"""
import copy
import importlib
import io
import json
import os
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from botocore.exceptions import ClientError

API_DIR = Path(__file__).resolve().parents[2] / "api"
sys.path.insert(0, str(API_DIR))


def _err(code, op):
    return ClientError({"Error": {"Code": code, "Message": code}}, op)


class FakeS3:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.presigned: list[tuple[str, dict]] = []

    def generate_presigned_url(self, op, Params, ExpiresIn):  # noqa: N803
        self.presigned.append((op, Params))
        return f"https://signed.example/{op}/{Params['Key']}?e={ExpiresIn}"

    def get_object(self, Bucket, Key):  # noqa: N803
        if Key not in self.objects:
            raise _err("NoSuchKey", "GetObject")
        return {"Body": io.BytesIO(self.objects[Key])}

    def head_object(self, Bucket, Key):  # noqa: N803
        if Key not in self.objects:
            raise _err("404", "HeadObject")
        return {}

    def put_object(self, Bucket, Key, Body, IfNoneMatch=None, **kwargs):  # noqa: N803
        if IfNoneMatch == "*" and Key in self.objects:  # S3 conditional write
            raise _err("PreconditionFailed", "PutObject")
        self.objects[Key] = Body if isinstance(Body, (bytes, bytearray)) else str(Body).encode()

    def list_objects_v2(self, Bucket, Prefix, MaxKeys=1000):  # noqa: N803
        keys = sorted(k for k in self.objects if k.startswith(Prefix))[:MaxKeys]
        return {"KeyCount": len(keys), "Contents": [{"Key": k} for k in keys]}


class FakeTable:
    def __init__(self):
        self.items: dict[tuple[str, str], dict] = {}

    def put_item(self, Item):  # noqa: N803
        self.items[(Item["pk"], Item["sk"])] = copy.deepcopy(Item)

    def get_item(self, Key):  # noqa: N803
        item = self.items.get((Key["pk"], Key["sk"]))
        return {"Item": copy.deepcopy(item)} if item else {}

    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues, ExpressionAttributeNames=None,  # noqa: N803
                    ConditionExpression=None):  # noqa: N803
        names, values = ExpressionAttributeNames or {}, ExpressionAttributeValues
        item = self.items.setdefault((Key["pk"], Key["sk"]), dict(Key))
        add = re.search(r"ADD (\w+) (:\w+)", UpdateExpression)
        if ConditionExpression and add and add.group(1) in item and not item[add.group(1)] < values[":cap"]:
            raise _err("ConditionalCheckFailedException", "UpdateItem")
        if add:
            item[add.group(1)] = item.get(add.group(1), 0) + values[add.group(2)]
        set_part = UpdateExpression.split("SET", 1)[1] if "SET" in UpdateExpression else ""
        for name, value in re.findall(r"(#?\w+)\s*=\s*(:\w+)", set_part):
            item[names.get(name, name)] = copy.deepcopy(values[value])

    def query(self, KeyConditionExpression, IndexName=None, ScanIndexForward=True, Limit=None):  # noqa: N803
        attr, value = KeyConditionExpression._values[0].name, KeyConditionExpression._values[1]
        rows = [copy.deepcopy(i) for i in self.items.values() if i.get(attr) == value]
        rows.sort(key=lambda i: i.get("created_at") or "", reverse=not ScanIndexForward)
        return {"Items": rows[:Limit] if Limit else rows}


class FakeRuntime:
    def __init__(self, answer=None):
        self.calls = []
        self.answer = answer or {"status": "accepted"}

    def invoke_agent_runtime(self, **kwargs):
        self.calls.append(kwargs)
        return {"response": io.BytesIO(json.dumps(self.answer).encode())}


ENV = {"RUNS_TABLE": "t", "DATA_BUCKET": "test-bucket", "AWS_REGION": "us-east-1", "KMS_KEY_ID": "key-123",
       "AGENT_RUNTIME_ARN": "arn:aws:bedrock-agentcore:us-east-1:111122223333:runtime/r-1",
       "RUNS_PER_DAY": "3", "RUNS_PER_IP_HOUR": "2"}


def load_handler(**env):
    with mock.patch.dict(os.environ, {**ENV, **env}, clear=False):
        sys.modules.pop("handler", None)
        h = importlib.import_module("handler")
    h._s3_client, h._table, h._agentcore_client = FakeS3(), FakeTable(), FakeRuntime()
    return h


def event(method, path, body=None, query=None, ip="203.0.113.10"):
    """An API Gateway HTTP API (payload v2) event, with routeKey and pathParameters as the HttpApi sets them."""
    route_key, params = f"{method} {path}", {}
    import handler as h
    for pattern in h.ROUTES:
        m, p = pattern.split(" ", 1)
        hit = re.match("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", p) + "$", path)
        if m == method and hit:
            route_key, params = pattern, hit.groupdict()
            break
    return {"routeKey": route_key, "pathParameters": params, "queryStringParameters": query or None,
            "requestContext": {"http": {"method": method, "path": path, "sourceIp": ip}},
            "body": json.dumps(body) if body is not None else None}


def call(h, *args, **kwargs):
    resp = h.handler(event(*args, **kwargs), None)
    return resp["statusCode"], json.loads(resp["body"])


def files(*names, size=100):
    return {"files": [{"name": n, "size": size} for n in names]}


class UploadUrlsTests(unittest.TestCase):
    def setUp(self):
        self.h = load_handler()

    def test_one_url_per_file_with_size_and_kms(self):
        s, out = call(self.h, "POST", "/engagements/Acme Corp/upload-urls", files("01_call.txt", "02_email.eml"))
        self.assertEqual(s, 200)
        self.assertEqual(out["input_prefix"], "input/Acme_Corp/")  # space -> underscore
        self.assertEqual(set(out["urls"]), {"01_call.txt", "02_email.eml"})
        self.assertEqual(out["required_headers"]["x-amz-server-side-encryption"], "aws:kms")
        self.assertEqual(out["required_headers"]["x-amz-server-side-encryption-aws-kms-key-id"], "key-123")
        op, params = self.h._s3_client.presigned[0]
        self.assertEqual((op, params["Key"], params["ContentLength"], params["SSEKMSKeyId"]),
                         ("put_object", "input/Acme_Corp/01_call.txt", 100, "key-123"))

    def test_bucket_default_key_when_no_kms_key(self):
        h = load_handler(KMS_KEY_ID="")
        _, out = call(h, "POST", "/engagements/Acme/upload-urls", files("a.txt"))
        self.assertEqual(out["required_headers"], {"x-amz-server-side-encryption": "aws:kms"})

    def test_rejections(self):
        cases = {
            "bad type": files("malware.exe"),
            "traversal": files("../secret.txt"),
            "hidden": files(".env"),
            "empty list": {"files": []},
            "names only": {"files": ["a.txt"]},
            "no size": {"files": [{"name": "a.txt"}]},
            "too big": files("a.txt", size=26 * 1024 * 1024),
            "duplicates": files("a.txt", "a.txt"),
            "too many": files(*[f"{i}.txt" for i in range(201)]),
        }
        for label, body in cases.items():
            self.assertEqual(call(self.h, "POST", "/engagements/Acme/upload-urls", body)[0], 400, label)

    def test_bad_engagement_name(self):
        self.assertEqual(call(self.h, "POST", "/engagements/bad%2Fname/upload-urls", files("a.txt"))[0], 400)


class StartRunTests(unittest.TestCase):
    def setUp(self):
        self.h = load_handler()
        self.h._s3_client.objects["input/Acme_Corp/01_call.txt"] = b"hello"

    def test_invokes_runtime_and_records_the_run(self):
        s, out = call(self.h, "POST", "/runs", {"engagement": "Acme Corp", "instructions": "focus on security"})
        self.assertEqual(s, 202)
        self.assertEqual(out["status_key"], f"output/Acme_Corp/{out['run_id']}/status.json")
        call_ = self.h._agentcore_client.calls[0]
        sent = json.loads(call_["payload"].decode())
        self.assertEqual((sent["action"], sent["engagement"], sent["input_prefix"], sent["output_prefix"]),
                         ("start", "Acme Corp", "input/Acme_Corp/", "output/Acme_Corp/"))
        self.assertEqual(sent["options"], {"narrative": True, "instructions": "focus on security", "clarify": False})
        self.assertEqual(call_["qualifier"], "DEFAULT")
        self.assertGreaterEqual(len(call_["runtimeSessionId"]), 33)
        meta = self.h._table.items[(f"RUN#Acme_Corp/{out['run_id']}", "META")]
        self.assertEqual((meta["owner"], meta["status"], meta["engagement"]), ("shared", "starting", "Acme Corp"))

    def test_clarify_forwarded(self):
        call(self.h, "POST", "/runs", {"engagement": "Acme Corp", "clarify": True})
        self.assertTrue(json.loads(self.h._agentcore_client.calls[-1]["payload"])["options"]["clarify"])

    def test_validation(self):
        self.assertEqual(call(self.h, "POST", "/runs", {})[0], 400)
        self.assertEqual(call(self.h, "POST", "/runs", {"engagement": "Acme Corp", "instructions": "x" * 4001})[0], 400)
        self.assertEqual(call(self.h, "POST", "/runs", {"engagement": "Nobody"})[0], 400)  # no documents uploaded
        self.assertEqual(self.h._agentcore_client.calls, [])

    def test_cost_guards(self):
        self.assertEqual(call(self.h, "POST", "/runs", {"engagement": "Acme Corp"})[0], 202)
        self.assertEqual(call(self.h, "POST", "/runs", {"engagement": "Acme Corp"})[0], 202)
        s, out = call(self.h, "POST", "/runs", {"engagement": "Acme Corp"})
        self.assertEqual(s, 429)
        self.assertIn("an hour", out["error"])
        self.assertEqual(call(self.h, "POST", "/runs", {"engagement": "Acme Corp"}, ip="198.51.100.1")[0], 202)
        s, out = call(self.h, "POST", "/runs", {"engagement": "Acme Corp"}, ip="198.51.100.2")
        self.assertEqual(s, 429)
        self.assertIn("daily", out["error"])
        self.assertFalse(any("203.0.113" in pk for pk, _ in self.h._table.items))  # IPs only as hashes
        self.assertEqual(call(self.h, "GET", "/session")[1]["runs_today"], 3)

    def test_runtime_rejection_is_502_and_records_nothing(self):
        self.h._agentcore_client = FakeRuntime({"status": "error", "error": "bad"})
        self.assertEqual(call(self.h, "POST", "/runs", {"engagement": "Acme Corp"})[0], 502)
        self.assertFalse(any(sk == "META" for _, sk in self.h._table.items))


class StatusResultsHistoryTests(unittest.TestCase):
    RID = "2026-01-01T00-00-00Z-abc123"

    def setUp(self):
        self.h = load_handler()
        self.base = f"output/Acme/{self.RID}/"

    def status(self, state, **extra):
        doc = {"state": state, "completed_steps": [], "steps": {},
               "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **extra}
        self.h._s3_client.objects[self.base + "status.json"] = json.dumps(doc).encode()

    def get(self, suffix=""):
        return call(self.h, "GET", f"/runs/{self.RID}{suffix}", query={"engagement": "Acme"})

    def test_unknown_run_404(self):
        self.assertEqual(self.get()[0], 404)
        self.assertEqual(call(self.h, "GET", "/runs/..", query={"engagement": "Acme"})[0], 404)

    def test_access_denied_maps_to_404(self):
        def deny(Bucket, Key):  # noqa: N803
            raise _err("AccessDenied", "GetObject")
        self.h._s3_client.get_object = deny
        self.assertEqual(self.get()[0], 404)

    def test_status_doc_and_cached_meta(self):
        self.h._table.put_item(Item={"pk": f"RUN#Acme/{self.RID}", "sk": "META", "run_id": self.RID,
                                     "engagement": "Acme", "owner": "shared", "status": "starting",
                                     "created_at": "2026-01-01T00:00:00Z"})
        self.status("succeeded", completed_steps=["load_documents"])
        s, doc = self.get()
        self.assertEqual((s, doc["state"]), (200, "succeeded"))
        meta = self.h._table.items[(f"RUN#Acme/{self.RID}", "META")]
        self.assertEqual((meta["status"], meta["steps_done"]), ("succeeded", 1))
        runs = call(self.h, "GET", "/runs")[1]["runs"]
        self.assertEqual([(r["run_id"], r["status"]) for r in runs], [(self.RID, "succeeded")])

    def test_started_but_not_written_yet_is_starting(self):
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.h._table.put_item(Item={"pk": f"RUN#Acme/{self.RID}", "sk": "META", "owner": "shared",
                                     "created_at": now, "status": "starting"})
        self.assertEqual(self.get()[1]["state"], "starting")

    def test_stale_running_is_failed(self):
        old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(timespec="seconds")
        self.status("running", updated_at=old)
        self.assertEqual(self.get()[1]["state"], "failed")

    def test_results_409_until_succeeded_then_present_files_only(self):
        self.status("running")
        self.assertEqual(self.get("/results")[0], 409)
        self.status("succeeded")
        for name in ("discovery.json", "README.md", "discovery_report.docx"):  # narrative.json absent
            self.h._s3_client.objects[self.base + name] = b"x"
        s, out = self.get("/results")
        self.assertEqual(s, 200)
        self.assertEqual(set(out["urls"]), {"discovery.json", "README.md", "discovery_report.docx"})


class ClarificationTests(unittest.TestCase):
    RID = "r1"

    def setUp(self):
        self.h = load_handler()
        self.h._s3_client.objects["output/Acme/r1/status.json"] = json.dumps(
            {"state": "awaiting_clarification", "updated_at": datetime.now(timezone.utc).isoformat()}).encode()
        self.h._s3_client.objects["work/Acme/r1/clarifications.json"] = json.dumps(
            {"questions": [{"id": "Q-1", "question": "Same?", "type": "conflict"}]}).encode()

    def test_questions(self):
        s, out = call(self.h, "GET", "/runs/r1/questions", query={"engagement": "Acme"})
        self.assertEqual((s, out["questions"][0]["id"]), (200, "Q-1"))

    def test_answers_store_and_resume_once(self):
        body = {"engagement": "Acme", "answers": {"Q-1": "same person"}}
        s, out = call(self.h, "POST", "/runs/r1/answers", body)
        self.assertEqual((s, out["resumed"]), (202, True))
        self.assertIn("work/Acme/r1/answers.json", self.h._s3_client.objects)
        sent = json.loads(self.h._agentcore_client.calls[-1]["payload"])
        self.assertEqual((sent["action"], sent["run_id"]), ("resume_with_answers", "r1"))
        self.assertEqual(call(self.h, "POST", "/runs/r1/answers", body)[0], 409)  # second submit
        self.assertEqual(len(self.h._agentcore_client.calls), 1)

    def test_bad_answers_and_wrong_state(self):
        self.assertEqual(call(self.h, "POST", "/runs/r1/answers", {"engagement": "Acme", "answers": "no"})[0], 400)
        self.assertEqual(call(self.h, "POST", "/runs/r1/answers", {"engagement": "Acme", "answers": {"Q": 1}})[0], 400)
        self.h._s3_client.objects["output/Acme/r1/status.json"] = b'{"state": "running"}'
        self.assertEqual(call(self.h, "GET", "/runs/r1/questions", query={"engagement": "Acme"})[0], 409)
        self.assertEqual(call(self.h, "POST", "/runs/r1/answers", {"engagement": "Acme", "answers": {}})[0], 409)


class S3StoreTests(unittest.TestCase):
    """No RUNS_TABLE: history and cost guards live in the data bucket (history/, guard/)."""

    def setUp(self):
        self.h = load_handler(RUNS_TABLE="")
        self.s3 = self.h._s3_client
        self.s3.objects["input/Acme_Corp/01_call.txt"] = b"hello"

    def start(self, ip="203.0.113.10", engagement="Acme Corp"):
        return call(self.h, "POST", "/runs", {"engagement": engagement}, ip=ip)

    def test_uses_the_bucket_store_without_a_table(self):
        self.assertIsInstance(self.h.store(), self.h.S3Store)
        with_table = load_handler()
        self.assertIsInstance(with_table.store(), with_table.DynamoStore)

    def test_guards_hourly_per_ip_and_daily_overall(self):
        self.assertEqual(self.start()[0], 202)
        self.assertEqual(self.start()[0], 202)
        s, out = self.start()
        self.assertEqual(s, 429)
        self.assertIn("an hour", out["error"])
        self.assertEqual(self.start(ip="198.51.100.1")[0], 202)
        s, out = self.start(ip="198.51.100.2")
        self.assertEqual(s, 429)
        self.assertIn("daily", out["error"])
        self.assertEqual(call(self.h, "GET", "/session")[1]["runs_today"], 3)
        self.assertFalse(any("203.0.113" in k for k in self.s3.objects))  # IPs only as hashes
        self.assertEqual(len([k for k in self.s3.objects if k.startswith("guard/DAY/")]), 3)

    def test_a_taken_slot_is_skipped_not_overwritten(self):
        day = f"guard/DAY/{self.h._now():%Y-%m-%d}/"
        # a concurrent request took slot 1 after we counted zero: we must land on slot 2 and keep the other's object
        with mock.patch.object(self.h.S3Store, "_count", lambda s, p, n: 0):
            self.s3.objects[day + "1"] = b"other request"
            self.h.store()._take(day, 3, "full")
        self.assertEqual((self.s3.objects[day + "1"], day + "2" in self.s3.objects), (b"other request", True))

    def test_slots_full_is_429(self):
        day = f"guard/DAY/{self.h._now():%Y-%m-%d}/"
        for n in range(1, 4):
            self.s3.objects[day + str(n)] = b""
        self.assertEqual(self.start()[0], 429)

    def test_conditional_write_errors_other_than_taken_slot_surface(self):
        def deny(**kwargs):
            raise _err("AccessDenied", "PutObject")
        self.s3.put_object = deny
        self.assertEqual(self.start()[0], 500)

    def test_history_lists_newest_first_with_live_status(self):
        old = "2026-01-01T00-00-00Z-aaaaaa"
        new = "2026-02-01T00-00-00Z-bbbbbb"
        for rid, at in ((old, "2026-01-01T00:00:00Z"), (new, "2026-02-01T00:00:00Z")):
            self.h.store().put_meta("Acme Corp", rid, {"created_at": at, "clarify": False, "status": "starting"})
        self.s3.objects[f"output/Acme_Corp/{new}/status.json"] = json.dumps(
            {"state": "succeeded", "completed_steps": ["a", "b"], "updated_at": "2026-02-01T00:03:00Z"}).encode()
        runs = call(self.h, "GET", "/runs")[1]["runs"]
        self.assertEqual([r["run_id"] for r in runs], [new, old])
        self.assertEqual((runs[0]["status"], runs[0]["steps_done"], runs[0]["finished_at"]),
                         ("succeeded", 2, "2026-02-01T00:03:00Z"))
        self.assertEqual(runs[1]["status"], "failed")  # started long ago, the agent never wrote a status

    def test_started_run_is_found_by_id_and_other_engagements_do_not_see_it(self):
        s, out = self.start()
        self.assertEqual(s, 202)
        got = call(self.h, "GET", f"/runs/{out['run_id']}", query={"engagement": "Acme Corp"})
        self.assertEqual((got[0], got[1]["state"]), (200, "starting"))
        self.assertEqual(call(self.h, "GET", f"/runs/{out['run_id']}", query={"engagement": "Other"})[0], 404)

    def test_run_id_route_parameter_name_is_accepted(self):
        """The manually created API declares GET /runs/{run_id}; template.yaml declares {id}."""
        self.s3.objects["output/Acme/r1/status.json"] = json.dumps(
            {"state": "running", "updated_at": self.h._iso()}).encode()
        ev = {"routeKey": "GET /runs/{run_id}", "pathParameters": {"run_id": "r1"},
              "queryStringParameters": {"engagement": "Acme"}, "requestContext": {"http": {"sourceIp": "1.2.3.4"}}}
        resp = self.h.handler(ev, None)
        self.assertEqual((resp["statusCode"], json.loads(resp["body"])["state"]), (200, "running"))


class RoutingAndHardeningTests(unittest.TestCase):
    def setUp(self):
        self.h = load_handler()

    def test_unknown_route_404(self):
        self.assertEqual(call(self.h, "GET", "/nope")[0], 404)

    def test_oversize_and_malformed_bodies(self):
        self.assertEqual(call(self.h, "POST", "/runs", {"engagement": "Acme", "pad": "x" * (300 * 1024)})[0], 413)
        ev = event("POST", "/runs")
        ev["body"] = "{not json"
        self.assertEqual(self.h.handler(ev, None)["statusCode"], 400)

    def test_unexpected_error_is_500_without_details(self):
        def boom(**kwargs):
            raise RuntimeError("secret detail")
        self.h._table.get_item = boom
        s, out = call(self.h, "GET", "/session")
        self.assertEqual((s, out), (500, {"error": "internal error"}))

    def test_clients_use_regional_virtual_hosts_and_short_invoke_timeout(self):
        with mock.patch.dict(os.environ, {"AWS_ACCESS_KEY_ID": "AKIDEXAMPLE", "AWS_SECRET_ACCESS_KEY": "x",
                                          "AWS_SESSION_TOKEN": ""}):
            client = self.h.make_s3_client()
            url = client.generate_presigned_url(
                "put_object", Params={"Bucket": "test-bucket", "Key": "input/A/a.txt", "ContentLength": 5,
                                      "ServerSideEncryption": "aws:kms"}, ExpiresIn=60)
            self.assertTrue(url.startswith("https://test-bucket.s3.us-east-1.amazonaws.com/input/A/a.txt?"), url)
            signed = re.search(r"X-Amz-SignedHeaders=([^&]+)", url).group(1)
            self.assertIn("content-length", signed)
            self.assertIn("x-amz-server-side-encryption", signed)
            self.assertEqual(self.h.make_agentcore_client().meta.config.read_timeout, 25)


if __name__ == "__main__":
    unittest.main()
