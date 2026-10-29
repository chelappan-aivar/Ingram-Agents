import io
import tempfile
import unittest
from unittest import mock

from botocore.exceptions import ClientError

from discovery_agent.data.storage import LocalStorage, S3Storage


class FakeS3:
    def __init__(self):
        self.objs, self.put_extra = {}, []

    def get_paginator(self, name):
        outer = self

        class Pages:
            def paginate(self, Bucket, Prefix):
                keys = sorted(k for k in outer.objs if k.startswith(Prefix))
                yield {"Contents": [{"Key": k} for k in keys[:1]]}  # two pages to exercise pagination
                yield {"Contents": [{"Key": k} for k in keys[1:]]}
        return Pages()

    def put_object(self, Bucket, Key, Body, **extra):
        self.objs[Key] = Body
        self.put_extra.append(extra)

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objs[Key])}

    def head_object(self, Bucket, Key):
        if Key not in self.objs:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")


class LocalStorageTests(unittest.TestCase):
    def test_round_trip_list_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = LocalStorage(tmp)
            st.write_bytes("a/b/one.txt", b"1")
            st.write_bytes("a/two.txt", b"2")
            st.write_bytes("z/three.txt", b"3")
            self.assertEqual(st.read_bytes("a/b/one.txt"), b"1")
            self.assertEqual(st.list_keys("a/"), ["a/b/one.txt", "a/two.txt"])
            self.assertTrue(st.exists("z/three.txt"))
            self.assertFalse(st.exists("z/missing.txt"))

    def test_keys_cannot_escape_the_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = LocalStorage(tmp)
            for bad in ("../x", "a/../../x", "/abs", ""):
                with self.assertRaises(ValueError):
                    st.write_bytes(bad, b"x")


class S3StorageTests(unittest.TestCase):
    def test_writes_use_kms_encryption(self):
        fake = FakeS3()
        st = S3Storage("bkt", client=fake, kms_key_id="key-1")
        st.write_bytes("output/x.json", b"{}")
        self.assertEqual(fake.put_extra[0], {"ServerSideEncryption": "aws:kms", "SSEKMSKeyId": "key-1"})
        S3Storage("bkt", client=fake).write_bytes("output/y.json", b"{}")
        self.assertEqual(fake.put_extra[1], {"ServerSideEncryption": "aws:kms"})

    def test_list_read_exists(self):
        fake = FakeS3()
        st = S3Storage("bkt", client=fake)
        for k in ("input/a", "input/b", "other/c"):
            st.write_bytes(k, k.encode())
        self.assertEqual(st.list_keys("input/"), ["input/a", "input/b"])
        self.assertEqual(st.read_bytes("input/a"), b"input/a")
        self.assertTrue(st.exists("input/b"))
        self.assertFalse(st.exists("input/zzz"))

    def test_invalid_keys_rejected(self):
        with self.assertRaises(ValueError):
            S3Storage("bkt", client=FakeS3()).read_bytes("../secrets")


class DefaultStorageTests(unittest.TestCase):
    def pick(self, bucket, dev, use_s3_on_dev=False):
        from discovery_agent import settings
        from discovery_agent.data import storage

        with mock.patch.object(settings, "S3_BUCKET", bucket), mock.patch.object(settings, "has_local_profile", lambda: dev), \
                mock.patch.object(settings, "USE_S3_ON_DEV_MACHINE", use_s3_on_dev), \
                mock.patch.object(storage.S3Storage, "__init__", lambda *a, **k: None):
            return storage.default_storage()

    def test_dev_machine_uses_local_files_and_runtime_uses_s3(self):
        from discovery_agent.data.storage import LocalStorage, S3Storage

        self.assertIsInstance(self.pick("bkt", dev=True), LocalStorage)
        self.assertIsInstance(self.pick("bkt", dev=False), S3Storage)
        self.assertIsInstance(self.pick("bkt", dev=True, use_s3_on_dev=True), S3Storage)

    def test_runtime_without_a_bucket_fails_loudly(self):
        with self.assertRaises(RuntimeError):  # never silently write to the container disk
            self.pick(None, dev=False)


if __name__ == "__main__":
    unittest.main()
