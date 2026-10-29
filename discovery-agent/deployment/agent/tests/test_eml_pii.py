"""Tests for the .eml PII guardrail: expose sender/recipient NAMES and content only; redact contact details.

These run fully offline (no AWS, no models). They also assert that non-email paths are unaffected, so the current
working state cannot regress.
"""
import tempfile
import unittest
from pathlib import Path

from discovery_agent.data.documents import Document, load_document, redact_pii

EML = """From: Bo Chen <bo.chen@acme.com>
To: "Dana Ruiz" <dana.ruiz@ingram.com>, ops-team@ingram.com
Cc: secret.exec@acme.com
Date: Mon, 22 Sep 2026 10:04:00 -0700
Subject: Re: [EXTERNAL]Re: Billing disputes

Hi Dana,

Call me on +1 (415) 555-0199 or email bo.chen@acme.com.
We keep chasing credit approvals. See https://acme.sharepoint.com/partner-rules for the discount rules.

Thanks,
Bo
"""


def _write(tmp: str, name: str, content: str) -> Path:
    p = Path(tmp) / name
    p.write_text(content, encoding="utf-8")
    return p


class RedactPiiUnitTests(unittest.TestCase):
    def test_email_addresses_redacted(self):
        self.assertEqual(redact_pii("reach me at a.b@c.com please"), "reach me at [email] please")

    def test_phone_redacted(self):
        self.assertIn("[phone]", redact_pii("call +1 (415) 555-0199 now"))

    def test_url_redacted(self):
        self.assertEqual(redact_pii("see https://x.com/y here"), "see [link] here")

    def test_plain_text_and_small_numbers_untouched(self):
        # ordinary prose and short numbers (e.g. '40 disputes', 'about 2 days') must survive
        s = "We handle 40 disputes a month, about 2 days each."
        self.assertEqual(redact_pii(s), s)


class EmlParsingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = _write(self.tmp.name, "02_email.eml", EML)
        self.doc = load_document(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_classified_as_email(self):
        self.assertEqual(self.doc.kind, "email")

    def test_sender_and_recipient_names_kept(self):
        self.assertIn("From: Bo Chen", self.doc.text)
        self.assertIn("Dana Ruiz", self.doc.text)

    def test_subject_kept_without_pii(self):
        self.assertIn("Billing disputes", self.doc.text)

    def test_email_addresses_removed_everywhere(self):
        self.assertNotIn("bo.chen@acme.com", self.doc.text)
        self.assertNotIn("dana.ruiz@ingram.com", self.doc.text)
        self.assertNotIn("ops-team@ingram.com", self.doc.text)
        self.assertNotIn("@", self.doc.text)  # no stray address anywhere

    def test_cc_and_date_dropped(self):
        self.assertNotIn("secret.exec", self.doc.text)   # Cc never exposed
        self.assertNotIn("Cc:", self.doc.text)
        self.assertNotIn("Date:", self.doc.text)
        self.assertNotIn("-0700", self.doc.text)

    def test_phone_and_links_redacted_in_body(self):
        self.assertNotIn("555-0199", self.doc.text)
        self.assertNotIn("sharepoint.com", self.doc.text)
        self.assertIn("[phone]", self.doc.text)
        self.assertIn("[link]", self.doc.text)

    def test_content_preserved(self):
        self.assertIn("chasing credit approvals", self.doc.text)
        self.assertIn("Hi Dana", self.doc.text)


class NonEmailPathsUnaffectedTests(unittest.TestCase):
    """The guardrail must only touch .eml. Plain text / transcripts must be byte-for-byte as before."""

    def test_plain_text_not_redacted(self):
        with tempfile.TemporaryDirectory() as tmp:
            # a .txt that happens to contain an email address must NOT be redacted (only .eml is guarded)
            p = _write(tmp, "note.txt", "Email the owner at owner@acme.com about the 40 disputes.")
            doc = load_document(p)
            self.assertEqual(doc.kind, "document")
            self.assertIn("owner@acme.com", doc.text)  # untouched

    def test_transcript_still_detected_and_cleaned(self):
        ts = "\n".join(f"Speaker {i%2}\n{i}:0{i%6}\nline {i}" for i in range(1, 13))
        with tempfile.TemporaryDirectory() as tmp:
            p = _write(tmp, "call.txt", ts)
            doc = load_document(p)
            self.assertEqual(doc.kind, "transcript")


if __name__ == "__main__":
    unittest.main()
