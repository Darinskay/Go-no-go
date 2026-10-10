#!/usr/bin/env python3
"""Deterministic helpers for the Go No-Go skill.

Everything that must behave the same way every time lives here: the gate
check, the slug, the idea hash, the ledger, and saving the record. All
judgment lives in SKILL.md.

The script only ever reads or writes inside <root>/.go-no-go/ (root defaults
to the current directory). It uses the Python standard library only.

Commands (each prints one line of JSON):

  start                      message on stdin -> gate, ledger check, new record
  save SLUG FILE             round text on stdin -> validated and saved
  show SLUG [FILE ...]       print saved record files
  finish SLUG --verdict GO|NO-GO --round N
                             plain-language reasons on stdin -> VERDICT.md + ledger

Exit codes: 0 ok, 1 rejected/refused/invalid input, 2 usage or state error.
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import sys
import tempfile
import unicodedata

GATE_PHRASE = "go or no-go"
QUOTE_CHARS = "\"'`“”‘’«»„‚‹›"
TRAILING_PUNCT = ".!?,;:…"

DIR_NAME = ".go-no-go"
LEDGER_NAME = "ledger.json"
IDEA_FILE = "00-idea.md"
VERDICT_FILE = "VERDICT.md"
ROUND_FILES = ["01-triage.md", "02-skeptic.md", "03-research.md", "04-memo.md"]
ROUND_NAMES = {1: "Triage", 2: "Skeptic", 3: "Researchers", 4: "Leadership review", 5: "Final call"}
MEMO_WORD_LIMIT = 300

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
RESULT_RE = re.compile(
    r"^\W*RESULT\W*:\W*(PASS|KILL|NOT\s+RUN)\b\W*(.*?)\W*$",
    re.IGNORECASE,
)
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
STOPWORDS = {
    "a", "an", "the", "for", "to", "of", "and", "or", "that", "with", "in",
    "on", "at", "by", "i", "im", "my", "we", "our", "is", "it", "its", "this",
    "be", "will", "would", "want", "idea", "who", "which", "so", "as", "are",
}


class GoNoGoError(Exception):
    """A problem the caller should report and stop on."""


# ---------- pure functions (unit tested) ----------

def normalize_gate_line(line):
    """Lowercase, drop quotes, trailing punctuation and extra whitespace."""
    text = unicodedata.normalize("NFKC", line)
    text = "".join(ch for ch in text if ch not in QUOTE_CHARS)
    text = text.strip().rstrip(TRAILING_PUNCT + " \t").strip()
    text = re.sub(r"\s+", " ", text)
    return text.casefold()


def check_gate(message):
    """Return (ok, reason, idea_text). idea_text is the message minus the gate line."""
    lines = (message or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return False, 'Rejected: the message is empty. End it with the line "go or no-go".', ""
    last = lines[-1]
    if normalize_gate_line(last) != GATE_PHRASE:
        shown = last.strip()
        if len(shown) > 60:
            shown = shown[:57] + "..."
        return (
            False,
            'Rejected without reading: the last line must be "go or no-go" '
            '(yours was "%s").' % shown,
            "",
        )
    idea = "\n".join(lines[:-1]).strip()
    return True, "", idea


def normalize_idea(idea):
    """Canonical form used for hashing: case, spacing and punctuation don't matter."""
    text = unicodedata.normalize("NFKC", idea or "").casefold()
    text = re.sub(r"[\W_]+", " ", text, flags=re.UNICODE)
    return text.strip()


def idea_hash(idea):
    return hashlib.sha256(normalize_idea(idea).encode("utf-8")).hexdigest()


def make_slug(idea, digest=None, max_words=6, max_len=40):
    digest = digest or idea_hash(idea)
    ascii_text = unicodedata.normalize("NFKD", normalize_idea(idea))
    ascii_text = ascii_text.encode("ascii", "ignore").decode("ascii")
    words = [w for w in re.findall(r"[a-z0-9]+", ascii_text) if w not in STOPWORDS]
    base = ""
    for word in words[:max_words]:
        candidate = word[:max_len] if not base else base + "-" + word
        if len(candidate) > max_len:
            break
        base = candidate
    return "%s-%s" % (base or "idea", digest[:6])


def parse_results(text):
    """Return a list of (status, detail) for every RESULT line, status in PASS/KILL/NOT RUN."""
    found = []
    for line in text.splitlines():
        m = RESULT_RE.match(line.strip())
        if m:
            status = re.sub(r"\s+", " ", m.group(1).upper())
            found.append((status, m.group(2).strip()))
    return found


def round_result(filename, text):
    """Validate a round's text and return {'result': ..., 'reason': ...} or raise."""
    if not text.strip():
        raise GoNoGoError("%s is empty." % filename)

    if filename == "04-memo.md":
        if parse_results(text):
            raise GoNoGoError("The memo must not contain a RESULT line.")
        words = count_words(text)
        if words > MEMO_WORD_LIMIT:
            raise GoNoGoError(
                "The memo has %d words; the limit is %d. Shorten it and save again."
                % (words, MEMO_WORD_LIMIT)
            )
        return {"result": "MEMO", "words": words}

    results = parse_results(text)
    if not results:
        raise GoNoGoError(
            "%s has no RESULT line. End it with 'RESULT: PASS' or 'RESULT: KILL: <reason>'."
            % filename
        )

    if filename != "03-research.md":
        status, detail = results[-1]
        if status == "NOT RUN":
            raise GoNoGoError("Only Round 3 can be marked NOT RUN.")
        if status == "KILL" and not detail:
            raise GoNoGoError("A KILL needs a reason: 'RESULT: KILL: <reason>'.")
        return {"result": status, "reason": detail}

    return research_result(text)


def research_result(text):
    """Combine the three researchers. Any sourced KILL kills; NOT RUN only if nothing ran."""
    sections = []
    chunk = []
    for line in text.splitlines():
        chunk.append(line)
        m = RESULT_RE.match(line.strip())
        if m:
            status = re.sub(r"\s+", " ", m.group(1).upper())
            sections.append((status, m.group(2).strip(), "\n".join(chunk)))
            chunk = []
    statuses = [s for s, _, _ in sections]

    if all(s == "NOT RUN" for s in statuses):
        reason = sections[0][1] or "web search was not available"
        return {"result": "NOT RUN", "reason": reason}
    if len(sections) != 3:
        raise GoNoGoError(
            "Round 3 needs exactly three researcher sections, each ending in a RESULT line "
            "(found %d)." % len(sections)
        )
    for i, (status, detail, body) in enumerate(sections, 1):
        if status == "KILL" and not detail:
            raise GoNoGoError("Researcher %d's KILL needs a reason." % i)
        if status in ("PASS", "KILL") and not URL_RE.search(body):
            raise GoNoGoError(
                "Researcher %d makes claims with no source URL. Every claim needs a URL, "
                "or mark the section 'RESULT: NOT RUN: <why>'." % i
            )
    kills = [detail for status, detail, _ in sections if status == "KILL"]
    if kills:
        return {"result": "KILL", "reason": " | ".join(kills)}
    return {"result": "PASS", "reason": ""}


def count_words(text):
    """Words as a reader counts them: list markers like '1.' and lone symbols don't count."""
    return sum(
        1 for tok in text.split()
        if re.search(r"\w", tok, re.UNICODE) and not re.match(r"^\d+[.)]$", tok)
    )


# ---------- filesystem (always inside <root>/.go-no-go) ----------

class Store(object):
    def __init__(self, root):
        self.root = os.path.abspath(root)
        self.base = os.path.join(self.root, DIR_NAME)
        self.ledger_path = os.path.join(self.base, LEDGER_NAME)

    def rel(self, *parts):
        return "/".join([DIR_NAME] + list(parts))

    def record_dir(self, slug):
        if not SLUG_RE.match(slug or ""):
            raise GoNoGoError("Invalid slug: %r" % slug)
        path = os.path.join(self.base, slug)
        if os.path.dirname(os.path.abspath(path)) != self.base:
            raise GoNoGoError("Refusing to use a path outside %s/." % DIR_NAME)
        return path

    def load_ledger(self):
        if not os.path.exists(self.ledger_path):
            return {"version": 1, "ideas": []}
        try:
            with open(self.ledger_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (ValueError, OSError) as exc:
            raise GoNoGoError("The ledger at %s is unreadable (%s). Not touching it." % (self.rel(LEDGER_NAME), exc))
        if not isinstance(data, dict) or not isinstance(data.get("ideas"), list):
            raise GoNoGoError("The ledger at %s has an unexpected format. Not touching it." % self.rel(LEDGER_NAME))
        return data

    def find(self, digest):
        for entry in self.load_ledger()["ideas"]:
            if entry.get("hash") == digest:
                return entry
        return None

    def write(self, path, text):
        directory = os.path.abspath(os.path.dirname(path))
        if directory != self.base and not directory.startswith(self.base + os.sep):
            raise GoNoGoError("Refusing to write outside %s/." % DIR_NAME)
        if not os.path.isdir(directory):
            os.makedirs(directory)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)

    def read(self, slug, filename):
        path = os.path.join(self.record_dir(slug), filename)
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()


# ---------- commands ----------

def cmd_start(store, message):
    ok, reason, idea = check_gate(message)
    if not ok:
        return 1, {"status": "rejected", "reason": reason}

    digest = idea_hash(idea)
    previous = store.find(digest)
    if previous:
        return 1, {
            "status": "refused",
            "reason": "This idea was already judged on %s: %s (Round %s, %s). No re-runs."
            % (previous.get("decided_at", "?"), previous.get("verdict"), previous.get("round"),
               ROUND_NAMES.get(previous.get("round"), "?")),
            "verdict": previous.get("verdict"),
            "round": previous.get("round"),
            "record": store.rel(previous.get("slug", ""), ""),
        }

    slug = make_slug(idea, digest)
    directory = store.record_dir(slug)
    if os.path.isdir(directory):
        # An earlier run of this exact idea stopped before a verdict. Start it clean.
        for name in ROUND_FILES + [VERDICT_FILE]:
            path = os.path.join(directory, name)
            if os.path.exists(path):
                os.remove(path)
    store.write(os.path.join(directory, IDEA_FILE), "# The idea, exactly as submitted\n\n" + idea + "\n")
    return 0, {"status": "started", "slug": slug, "record": store.rel(slug, ""), "idea": idea}


def cmd_save(store, slug, filename, text):
    if filename not in ROUND_FILES:
        raise GoNoGoError("FILE must be one of: %s" % ", ".join(ROUND_FILES))
    directory = store.record_dir(slug)
    if not os.path.exists(os.path.join(directory, IDEA_FILE)):
        raise GoNoGoError("No record named %s. Run 'start' first." % slug)
    if os.path.exists(os.path.join(directory, VERDICT_FILE)):
        raise GoNoGoError("This record already has a verdict. It can't be changed.")

    index = ROUND_FILES.index(filename)
    for earlier in ROUND_FILES[:index]:
        content = store.read(slug, earlier)
        if content is None:
            raise GoNoGoError("Save %s before %s." % (earlier, filename))
        if earlier != "04-memo.md" and round_result(earlier, content)["result"] == "KILL":
            raise GoNoGoError("%s already killed this idea. Later rounds must not run." % earlier)

    result = round_result(filename, text)
    store.write(os.path.join(directory, filename), text.rstrip() + "\n")
    out = {"status": "saved", "file": store.rel(slug, filename)}
    out.update(result)
    return 0, out


def cmd_show(store, slug, files):
    names = files or [IDEA_FILE] + ROUND_FILES + [VERDICT_FILE]
    parts = []
    for name in names:
        if name not in [IDEA_FILE] + ROUND_FILES + [VERDICT_FILE]:
            raise GoNoGoError("Unknown record file: %s" % name)
        content = store.read(slug, name)
        if content is not None:
            parts.append("===== %s =====\n%s" % (name, content.rstrip()))
    if not parts:
        raise GoNoGoError("Nothing saved for %s." % slug)
    return 0, "\n\n".join(parts)


def cmd_finish(store, slug, verdict, round_no, reasons, today=None):
    if verdict not in ("GO", "NO-GO"):
        raise GoNoGoError("Verdict must be exactly GO or NO-GO (got %r)." % verdict)
    if round_no not in (1, 2, 3, 5):
        raise GoNoGoError("The deciding round must be 1, 2, 3 or 5.")
    if not reasons.strip():
        raise GoNoGoError("Give the reasons in plain language on stdin.")
    directory = store.record_dir(slug)
    idea_text = store.read(slug, IDEA_FILE)
    if idea_text is None:
        raise GoNoGoError("No record named %s." % slug)
    if os.path.exists(os.path.join(directory, VERDICT_FILE)):
        raise GoNoGoError("This record already has a verdict. It can't be changed.")

    if round_no in (1, 2, 3):
        if verdict != "NO-GO":
            raise GoNoGoError("Rounds 1-3 can only end an idea with NO-GO.")
        filename = ROUND_FILES[round_no - 1]
        content = store.read(slug, filename)
        if content is None or round_result(filename, content)["result"] != "KILL":
            raise GoNoGoError("%s is not saved as a KILL, so Round %d can't be the deciding round." % (filename, round_no))
    else:
        for filename in ROUND_FILES:
            if store.read(slug, filename) is None:
                raise GoNoGoError("Round 5 needs %s saved first." % filename)

    idea = idea_text.split("\n", 2)[-1].strip()
    digest = idea_hash(idea)
    if store.find(digest):
        raise GoNoGoError("This idea is already in the ledger.")

    today = today or datetime.date.today().isoformat()
    body = (
        "# Verdict: %s\n\n"
        "- Decided in: Round %d (%s)\n"
        "- Date: %s\n\n"
        "## Why\n\n%s\n\n"
        "## The record\n\n"
        "Read the files in this folder in order, 00 to 04. Rounds after the deciding one were not run.\n\n"
        "_This verdict is a second opinion from an AI review, not professional advice._\n"
        % (verdict, round_no, ROUND_NAMES[round_no], today, reasons.strip())
    )
    store.write(os.path.join(directory, VERDICT_FILE), body)

    ledger = store.load_ledger()
    ledger["ideas"].append(
        {"hash": digest, "slug": slug, "verdict": verdict, "round": round_no, "decided_at": today}
    )
    store.write(store.ledger_path, json.dumps(ledger, indent=2, ensure_ascii=False) + "\n")
    return 0, {
        "status": "finished",
        "verdict": verdict,
        "round": round_no,
        "round_name": ROUND_NAMES[round_no],
        "record": store.rel(slug, ""),
    }


def read_stdin():
    data = sys.stdin.buffer.read()
    return data.decode("utf-8", errors="replace")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Go No-Go helper (standard library only).")
    parser.add_argument("--root", default=".", help="Folder that holds .go-no-go/ (default: current folder)")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("start", help="Gate + ledger check. Message on stdin.")
    p_save = sub.add_parser("save", help="Save one round. Text on stdin.")
    p_save.add_argument("slug")
    p_save.add_argument("file")
    p_show = sub.add_parser("show", help="Print saved record files.")
    p_show.add_argument("slug")
    p_show.add_argument("files", nargs="*")
    p_finish = sub.add_parser("finish", help="Write VERDICT.md and the ledger. Reasons on stdin.")
    p_finish.add_argument("slug")
    p_finish.add_argument("--verdict", required=True)
    p_finish.add_argument("--round", type=int, required=True)
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 2

    store = Store(args.root)
    try:
        if args.command == "start":
            code, out = cmd_start(store, read_stdin())
        elif args.command == "save":
            code, out = cmd_save(store, args.slug, args.file, read_stdin())
        elif args.command == "show":
            code, out = cmd_show(store, args.slug, args.files)
        else:
            code, out = cmd_finish(store, args.slug, args.verdict, args.round, read_stdin())
    except GoNoGoError as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}, ensure_ascii=False))
        return 2

    if isinstance(out, str):
        print(out)
    else:
        print(json.dumps(out, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main())
