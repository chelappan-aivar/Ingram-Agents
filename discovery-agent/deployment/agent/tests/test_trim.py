import unittest

from discovery_agent.analysis import trim
from discovery_agent.data.documents import Document


def transcript(n_turns, filler):
    lines = []
    for i in range(n_turns):
        who = "Ana Lee" if i % 2 == 0 else "Bo Chen"
        lines += [who, f"{i // 60}:{i % 60:02d}", f"turn number {i} " + filler]
    return Document("t", "transcript", "\n".join(lines), {"Ana Lee", "Bo Chen"}, "Call")


BIG = transcript(400, "x" * 80)  # well over TRIM_MIN_CHARS


class SplitTests(unittest.TestCase):
    def test_transcript_turns_are_speaker_blocks_and_rejoin_exactly(self):
        turns, sep = trim.split_turns(BIG)
        self.assertEqual(len(turns), 400)
        self.assertTrue(turns[0].startswith("Ana Lee\n0:00"))
        self.assertEqual(sep.join(turns), BIG.text)

    def test_plain_documents_split_into_paragraphs(self):
        doc = Document("d", "document", "first para\n\nsecond para\n\n" + "w " * 3000, set(), "D")
        turns, sep = trim.split_turns(doc)
        self.assertEqual(turns[:2], ["first para", "second para"])
        self.assertGreater(len(turns), 3)
        self.assertTrue(all(len(t) <= 2000 for t in turns))

    def test_windows_cover_every_turn_without_overlap(self):
        turns = ["a" * 100_000] * 7
        w = trim.windows(turns)
        self.assertGreater(len(w), 1)
        self.assertEqual([i for a, b in w for i in range(a, b + 1)], list(range(7)))

    def test_range_problems(self):
        self.assertEqual(trim.range_problems({"keep": [[1, 3], [5, 5]]}, 1, 10), [])
        self.assertTrue(trim.range_problems({"keep": [[0, 3]]}, 1, 10))
        self.assertTrue(trim.range_problems({"keep": [[4, 2]]}, 1, 10))
        self.assertTrue(trim.range_problems({"keep": [["a", 2]]}, 1, 10))
        self.assertTrue(trim.range_problems({"nope": 1}, 1, 10))


class TrimTests(unittest.TestCase):
    def test_kept_text_is_a_verbatim_slice_of_the_original(self):
        ask = lambda prompt, lo, hi: {"keep": [[1, 100], [301, 400]]}  # noqa: E731
        out = trim.trim_document(BIG, ask)
        self.assertLess(out.kept_fraction, 1)
        self.assertEqual(out.kept_fraction, round(len(out.text) / len(BIG.text), 2))
        turns, sep = trim.split_turns(BIG)
        self.assertEqual(out.text, sep.join(turns[:100] + turns[300:]))
        self.assertIn("turn number 0 ", out.text)
        self.assertNotIn("turn number 150 ", out.text)
        self.assertEqual(out.speakers, {"Ana Lee", "Bo Chen"})
        self.assertEqual((out.id, out.kind, out.label), (BIG.id, BIG.kind, BIG.label))

    def test_small_documents_are_never_trimmed(self):
        small = transcript(10, "y")
        self.assertIs(trim.trim_document(small, lambda *a: self.fail("model must not be called")), small)

    def test_model_failure_keeps_the_whole_document(self):
        def ask(prompt, lo, hi):
            raise RuntimeError("model could not answer")
        self.assertIs(trim.trim_document(BIG, ask), BIG)

    def test_keeping_almost_nothing_is_not_trusted(self):
        self.assertIs(trim.trim_document(BIG, lambda p, lo, hi: {"keep": [[1, 1]]}), BIG)

    def test_prompt_numbers_turns_and_states_the_valid_range(self):
        seen = {}
        def ask(prompt, lo, hi):
            seen.update(prompt=prompt, lo=lo, hi=hi)
            return {"keep": [[lo, hi]]}
        trim.trim_document(BIG, ask)
        self.assertEqual((seen["lo"], seen["hi"]), (1, 400))
        self.assertIn("[T1] Ana Lee", seen["prompt"])
        self.assertIn("[T400] Bo Chen", seen["prompt"])


if __name__ == "__main__":
    unittest.main()
