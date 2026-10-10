"""Unit tests for skills/go-no-go/scripts/gonogo.py. Run: python3 -m unittest discover tests"""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "skills", "go-no-go", "scripts"))

import gonogo  # noqa: E402

IDEA = (
    "A booking tool for independent dog groomers in Ohio who lose about 5 hours a week "
    "to phone and Instagram DM bookings."
)
MESSAGE = IDEA + "\n\ngo or no-go"

RESEARCH_PASS = """# Round 3: Researchers

## Researcher A: Competitors and substitutes
- MoeGo serves groomers (https://example.com/moego)
**Fatal assumption check:**
- A1: No evidence
RESULT: PASS

## Researcher B: What people pay
- Plans start at $39/month (https://example.com/pricing)
RESULT: PASS

## Researcher C: Demand signals
- Reddit thread asking for this (https://example.com/reddit)
RESULT: PASS
"""


class GateTests(unittest.TestCase):
    def test_exact_phrase_passes(self):
        ok, reason, idea = gonogo.check_gate(MESSAGE)
        self.assertTrue(ok)
        self.assertEqual(reason, "")
        self.assertEqual(idea, IDEA)

    def test_case_quotes_and_trailing_punctuation_ignored(self):
        for last in ['Go or No-Go?', '"GO OR NO-GO"', "“go or no-go”.", "'go or no-go'!!", "  go  or  no-go  ", "`Go or no-go`?!"]:
            ok, _, idea = gonogo.check_gate("My idea\n" + last)
            self.assertTrue(ok, last)
            self.assertEqual(idea, "My idea")

    def test_trailing_blank_lines_ignored(self):
        ok, _, _ = gonogo.check_gate("My idea\ngo or no-go\n\n   \n")
        self.assertTrue(ok)

    def test_windows_line_endings(self):
        ok, _, idea = gonogo.check_gate("My idea\r\ngo or no-go\r\n")
        self.assertTrue(ok)
        self.assertEqual(idea, "My idea")

    def test_missing_phrase_rejected(self):
        ok, reason, idea = gonogo.check_gate("A great app for dog groomers.\nWhat do you think?")
        self.assertFalse(ok)
        self.assertIn("go or no-go", reason)
        self.assertIn("What do you think?", reason)
        self.assertEqual(idea, "")
        self.assertNotIn("\n", reason)

    def test_phrase_not_on_last_line_rejected(self):
        ok, _, _ = gonogo.check_gate("go or no-go\nA great app for dog groomers.")
        self.assertFalse(ok)

    def test_phrase_inside_sentence_rejected(self):
        ok, _, _ = gonogo.check_gate("Idea\nso is it go or no-go")
        self.assertFalse(ok)

    def test_variants_rejected(self):
        for last in ["go/no-go", "go or no go", "go or nogo", "go-no-go", "go or no-go please"]:
            ok, _, _ = gonogo.check_gate("Idea\n" + last)
            self.assertFalse(ok, last)

    def test_empty_rejected(self):
        for msg in ["", "   ", "\n\n", None]:
            ok, reason, _ = gonogo.check_gate(msg)
            self.assertFalse(ok)
            self.assertIn("empty", reason)

    def test_long_last_line_truncated_in_reason(self):
        ok, reason, _ = gonogo.check_gate("x" * 200)
        self.assertFalse(ok)
        self.assertLess(len(reason), 200)


class HashAndSlugTests(unittest.TestCase):
    def test_hash_ignores_case_spacing_punctuation(self):
        a = gonogo.idea_hash("A booking tool, for Dog groomers!")
        b = gonogo.idea_hash("  a   booking tool for dog-groomers ")
        self.assertEqual(a, b)
        self.assertEqual(len(a), 64)

    def test_hash_differs_for_different_ideas(self):
        self.assertNotEqual(gonogo.idea_hash("dog groomers"), gonogo.idea_hash("cat groomers"))

    def test_slug_shape(self):
        slug = gonogo.make_slug(IDEA)
        self.assertRegex(slug, r"^[a-z0-9]+(-[a-z0-9]+)*$")
        self.assertTrue(slug.startswith("booking-tool-independent-dog-groomers"))
        self.assertTrue(slug.endswith(gonogo.idea_hash(IDEA)[:6]))
        self.assertLessEqual(len(slug), 47)

    def test_slug_cuts_on_word_boundary(self):
        slug = gonogo.make_slug("FreshPaws fresh cooked dog food delivered by drone")
        self.assertTrue(slug.startswith("freshpaws-fresh-cooked-dog-food-"))
        self.assertNotIn("delivere-", slug)
        self.assertTrue(gonogo.make_slug("x" * 80).startswith("x" * 40 + "-"))

    def test_slug_non_latin_falls_back(self):
        slug = gonogo.make_slug("Застосунок для грумерів")
        self.assertRegex(slug, r"^idea-[0-9a-f]{6}$")

    def test_slug_accents_stripped(self):
        self.assertTrue(gonogo.make_slug("Café pour étudiants").startswith("cafe-pour-etudiants"))

    def test_slug_is_stable(self):
        self.assertEqual(gonogo.make_slug(IDEA), gonogo.make_slug(IDEA.upper()))


class ResultParsingTests(unittest.TestCase):
    def test_pass_and_kill(self):
        self.assertEqual(gonogo.round_result("01-triage.md", "x\nRESULT: PASS")["result"], "PASS")
        r = gonogo.round_result("01-triage.md", "x\nRESULT: KILL: nobody named.")
        self.assertEqual(r, {"result": "KILL", "reason": "nobody named"})

    def test_markdown_wrapped_result(self):
        r = gonogo.round_result("02-skeptic.md", "x\n**RESULT:** KILL — A2 has no support")
        self.assertEqual(r["result"], "KILL")
        self.assertEqual(r["reason"], "A2 has no support")

    def test_missing_result_errors(self):
        with self.assertRaises(gonogo.GoNoGoError):
            gonogo.round_result("01-triage.md", "no verdict here")

    def test_kill_without_reason_errors(self):
        with self.assertRaises(gonogo.GoNoGoError):
            gonogo.round_result("01-triage.md", "RESULT: KILL")

    def test_not_run_only_for_research(self):
        with self.assertRaises(gonogo.GoNoGoError):
            gonogo.round_result("02-skeptic.md", "RESULT: NOT RUN: no search")
        r = gonogo.round_result("03-research.md", "Web search was not available.\nRESULT: NOT RUN: web search not available")
        self.assertEqual(r["result"], "NOT RUN")

    def test_research_all_pass(self):
        self.assertEqual(gonogo.round_result("03-research.md", RESEARCH_PASS)["result"], "PASS")

    def test_research_one_kill_kills(self):
        text = RESEARCH_PASS.replace(
            "- Reddit thread asking for this (https://example.com/reddit)\nRESULT: PASS",
            "- Law bans it (https://example.com/law)\nRESULT: KILL: A1 contradicted: it is illegal (https://example.com/law)",
        )
        r = gonogo.round_result("03-research.md", text)
        self.assertEqual(r["result"], "KILL")
        self.assertIn("A1 contradicted", r["reason"])

    def test_research_needs_urls(self):
        text = RESEARCH_PASS.replace("(https://example.com/pricing)", "(trust me)")
        with self.assertRaises(gonogo.GoNoGoError) as ctx:
            gonogo.round_result("03-research.md", text)
        self.assertIn("Researcher 2", str(ctx.exception))

    def test_research_needs_three_sections(self):
        text = RESEARCH_PASS.split("## Researcher C")[0]
        with self.assertRaises(gonogo.GoNoGoError):
            gonogo.round_result("03-research.md", text)

    def test_research_partial_not_run_still_counts(self):
        text = RESEARCH_PASS.replace(
            "- Reddit thread asking for this (https://example.com/reddit)\nRESULT: PASS",
            "Web search was not available.\nRESULT: NOT RUN: web search not available",
        )
        self.assertEqual(gonogo.round_result("03-research.md", text)["result"], "PASS")

    def test_memo_word_limit(self):
        ok = "# Memo\n" + "word " * 299
        self.assertEqual(gonogo.round_result("04-memo.md", ok)["words"], 300)
        with self.assertRaises(gonogo.GoNoGoError):
            gonogo.round_result("04-memo.md", "word " * 301)

    def test_memo_punctuation_not_counted(self):
        self.assertEqual(gonogo.count_words("# Title\n1. one — two **three**"), 4)

    def test_memo_rejects_result_line(self):
        with self.assertRaises(gonogo.GoNoGoError):
            gonogo.round_result("04-memo.md", "memo\nRESULT: PASS")


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.store = gonogo.Store(self.root)

    def tearDown(self):
        shutil.rmtree(self.root)

    def start(self, message=MESSAGE):
        return gonogo.cmd_start(self.store, message)

    def all_files(self):
        found = []
        for dirpath, _, files in os.walk(self.root):
            for name in files:
                found.append(os.path.relpath(os.path.join(dirpath, name), self.root))
        return sorted(found)

    def test_rejected_message_writes_nothing(self):
        code, out = self.start("An idea with no gate line")
        self.assertEqual(code, 1)
        self.assertEqual(out["status"], "rejected")
        self.assertEqual(self.all_files(), [])

    def test_start_creates_idea_file_only(self):
        code, out = self.start()
        self.assertEqual(code, 0)
        self.assertEqual(out["status"], "started")
        self.assertEqual(out["record"], ".go-no-go/%s/" % out["slug"])
        self.assertEqual(self.all_files(), [os.path.join(".go-no-go", out["slug"], "00-idea.md")])
        self.assertIn(IDEA, self.store.read(out["slug"], "00-idea.md"))

    def test_kill_in_round_one_and_ledger_refusal(self):
        _, out = self.start()
        slug = out["slug"]
        _, saved = gonogo.cmd_save(self.store, slug, "01-triage.md", "x\nRESULT: KILL: no audience")
        self.assertEqual(saved["result"], "KILL")
        with self.assertRaises(gonogo.GoNoGoError):
            gonogo.cmd_save(self.store, slug, "02-skeptic.md", "x\nRESULT: PASS")
        code, fin = gonogo.cmd_finish(self.store, slug, "NO-GO", 1, "- No audience named.", today="2026-10-10")
        self.assertEqual(code, 0)
        self.assertEqual(fin["round_name"], "Triage")
        verdict = self.store.read(slug, "VERDICT.md")
        self.assertIn("# Verdict: NO-GO", verdict)
        self.assertIn("Round 1 (Triage)", verdict)

        with open(self.store.ledger_path) as fh:
            ledger = json.load(fh)
        self.assertEqual(len(ledger["ideas"]), 1)
        entry = ledger["ideas"][0]
        self.assertEqual(entry["hash"], gonogo.idea_hash(IDEA))
        self.assertEqual(entry["verdict"], "NO-GO")
        self.assertNotIn("idea", entry)

        code, again = self.start(IDEA.upper().replace(",", "") + "\nGo or No-Go?")
        self.assertEqual(code, 1)
        self.assertEqual(again["status"], "refused")
        self.assertIn("2026-10-10", again["reason"])
        self.assertEqual(again["record"], ".go-no-go/%s/" % slug)

    def test_full_run_to_round_five(self):
        _, out = self.start()
        slug = out["slug"]
        gonogo.cmd_save(self.store, slug, "01-triage.md", "x\nRESULT: PASS")
        gonogo.cmd_save(self.store, slug, "02-skeptic.md", "x\nRESULT: PASS")
        _, r3 = gonogo.cmd_save(self.store, slug, "03-research.md", RESEARCH_PASS)
        self.assertEqual(r3["result"], "PASS")
        _, r4 = gonogo.cmd_save(self.store, slug, "04-memo.md", "# Memo\nRecommendation: GO")
        self.assertEqual(r4["result"], "MEMO")
        with self.assertRaises(gonogo.GoNoGoError):
            gonogo.cmd_finish(self.store, slug, "go", 5, "- reason")
        with self.assertRaises(gonogo.GoNoGoError):
            gonogo.cmd_finish(self.store, slug, "GO", 4, "- reason")
        code, fin = gonogo.cmd_finish(self.store, slug, "GO", 5, "- Clear demand.")
        self.assertEqual((code, fin["verdict"], fin["round"]), (0, "GO", 5))
        with self.assertRaises(gonogo.GoNoGoError):
            gonogo.cmd_save(self.store, slug, "04-memo.md", "changed")
        with self.assertRaises(gonogo.GoNoGoError):
            gonogo.cmd_finish(self.store, slug, "NO-GO", 5, "- appeal")

    def test_rounds_must_be_in_order(self):
        _, out = self.start()
        with self.assertRaises(gonogo.GoNoGoError):
            gonogo.cmd_save(self.store, out["slug"], "02-skeptic.md", "x\nRESULT: PASS")

    def test_invalid_save_writes_nothing(self):
        _, out = self.start()
        with self.assertRaises(gonogo.GoNoGoError):
            gonogo.cmd_save(self.store, out["slug"], "01-triage.md", "no result line")
        self.assertIsNone(self.store.read(out["slug"], "01-triage.md"))

    def test_finish_round_must_match_kill(self):
        _, out = self.start()
        gonogo.cmd_save(self.store, out["slug"], "01-triage.md", "x\nRESULT: PASS")
        with self.assertRaises(gonogo.GoNoGoError):
            gonogo.cmd_finish(self.store, out["slug"], "NO-GO", 1, "- reason")
        with self.assertRaises(gonogo.GoNoGoError):
            gonogo.cmd_finish(self.store, out["slug"], "GO", 2, "- reason")

    def test_finish_needs_reasons(self):
        _, out = self.start()
        gonogo.cmd_save(self.store, out["slug"], "01-triage.md", "x\nRESULT: KILL: vague")
        with self.assertRaises(gonogo.GoNoGoError):
            gonogo.cmd_finish(self.store, out["slug"], "NO-GO", 1, "   ")

    def test_unfinished_run_restarts_clean(self):
        _, out = self.start()
        gonogo.cmd_save(self.store, out["slug"], "01-triage.md", "x\nRESULT: PASS")
        code, again = self.start()
        self.assertEqual(code, 0)
        self.assertEqual(again["slug"], out["slug"])
        self.assertIsNone(self.store.read(out["slug"], "01-triage.md"))

    def test_bad_slugs_and_files_refused(self):
        for slug in ["../evil", "a/b", "", "UPPER", ".hidden"]:
            with self.assertRaises(gonogo.GoNoGoError):
                self.store.record_dir(slug)
        _, out = self.start()
        for name in ["VERDICT.md", "00-idea.md", "../x.md", "notes.txt"]:
            with self.assertRaises(gonogo.GoNoGoError):
                gonogo.cmd_save(self.store, out["slug"], name, "x\nRESULT: PASS")

    def test_never_writes_outside_dir(self):
        with self.assertRaises(gonogo.GoNoGoError):
            self.store.write(os.path.join(self.root, "outside.txt"), "x")
        with self.assertRaises(gonogo.GoNoGoError):
            self.store.write(os.path.join(self.root, ".go-no-go-evil", "x.txt"), "x")

    def test_corrupt_ledger_is_not_overwritten(self):
        os.makedirs(self.store.base)
        with open(self.store.ledger_path, "w") as fh:
            fh.write("{not json")
        with self.assertRaises(gonogo.GoNoGoError):
            self.start()
        with open(self.store.ledger_path) as fh:
            self.assertEqual(fh.read(), "{not json")

    def test_show(self):
        _, out = self.start()
        gonogo.cmd_save(self.store, out["slug"], "01-triage.md", "triage text\nRESULT: PASS")
        _, text = gonogo.cmd_show(self.store, out["slug"], [])
        self.assertIn("===== 00-idea.md =====", text)
        self.assertIn("triage text", text)
        _, only = gonogo.cmd_show(self.store, out["slug"], ["01-triage.md"])
        self.assertNotIn("00-idea.md", only)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root)

    def run_cli(self, args, stdin=""):
        old_stdin = sys.stdin
        sys.stdin = io.TextIOWrapper(io.BytesIO(stdin.encode("utf-8")), encoding="utf-8")
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                code = gonogo.main(["--root", self.root] + args)
        finally:
            sys.stdin = old_stdin
        return code, buf.getvalue()

    def test_cli_prints_one_json_line(self):
        code, out = self.run_cli(["start"], "no gate")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["status"], "rejected")
        self.assertEqual(out.count("\n"), 1)

    def test_cli_error_exit_code(self):
        code, out = self.run_cli(["save", "missing-abc123", "01-triage.md"], "RESULT: PASS")
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["status"], "error")

    def test_cli_unicode_round_trip(self):
        code, out = self.run_cli(["start"], "Ідея для грумерів у Києві\ngo or no-go")
        self.assertEqual(code, 0)
        self.assertIn("Ідея", json.loads(out)["idea"])


if __name__ == "__main__":
    unittest.main()
