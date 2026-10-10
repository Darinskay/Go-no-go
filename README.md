# Go No-Go

**A strict second opinion on your idea, before you spend weeks building it.**

1. AI assistants tend to agree with your ideas. Go No-Go does the opposite: it tries to find a reason to kill yours.
2. You send one message describing the idea and end it with the line `go or no-go`.
3. The idea goes through five rounds: Triage, Skeptic, three Researchers who search the web, a Leadership memo, and a Final call.
4. It stops at the first round your idea fails, and tells you which round and why, in plain words.
5. You get one word back, **GO** or **NO-GO**, plus a written record of every round. No setup, no API keys.

---

## Install in Claude Code

Type these two lines into Claude Code, one at a time:

```
/plugin marketplace add Darinskay/Go-no-go
```

```
/plugin install go-no-go@go-no-go
```

Then run `/reload-plugins` (or close and reopen Claude Code).

## Install in the Claude app (claude.ai, desktop, mobile)

Custom skills work on Free, Pro, Max, Team and Enterprise plans. These steps come from Anthropic's help article [Using Skills in Claude](https://support.claude.com/en/articles/12512180-using-skills-in-claude), checked October 2026:

1. Download **`go-no-go.zip`** from the [latest release](https://github.com/Darinskay/Go-no-go/releases/latest). Don't unzip it.
2. In Claude, open **Settings > Capabilities** and turn on **Code execution and file creation**.
3. Go to **Customize > Skills**.
4. Click **+**, then **+ Create skill**, then **Upload a skill**, and choose `go-no-go.zip`.
5. Make sure the skill is switched on.

Turn on web search in your chat too, or Round 3 (the researchers) will be marked "not run".

What I couldn't confirm for the Claude app: whether skills there can run sub-agents (the rounds then run one after another in the same chat), and whether the record of past ideas survives between chats. The verdict works either way.

## How to use it

Start your message with `/go-no-go` in Claude Code, or say "Run this through Go No-Go" in the Claude app. Describe your idea: **who it's for, what problem it fixes, and any proof you have** (interviews, pre-orders, numbers). The last line must be exactly:

```
go or no-go
```

(Capital letters, quotes and a question mark at the end are fine.) To start a new line in Claude Code, press Shift+Enter, or type `\` and then Enter.

The rules:

- **One message.** It never asks you questions. It judges only what you wrote, so put your evidence in.
- **No appeals.** Arguing with the verdict won't change it. The same idea sent again is refused.
- **Your record** is saved in a `.go-no-go/` folder in the folder you're working in. Go No-Go never touches any other file.

## Example run

```
/go-no-go An app that uses AI to help people
go or no-go
```

> **NO-GO**, decided in Round 1 (Triage).
>
> - "People" isn't a group you can find, talk to or sell to.
> - "Help people" doesn't name a real problem the app fixes.
> - AI is how the app would work. It isn't a reason anyone would need it.
>
> Full record: `.go-no-go/app-uses-ai-help-people-db73de/`

The record folder holds one file per round that ran (`00-idea.md`, `01-triage.md`, ... `VERDICT.md`), so you can read exactly why.

## What the rounds check

| Round | What it does | It kills the idea when |
|---|---|---|
| 1. Triage | Looks for who it's for and what problem it fixes | Either is missing or too vague to act on |
| 2. Skeptic | Lists every assumption, stated or not, and checks your message for support | A make-or-break assumption has no support and no cheap way to test it |
| 3. Researchers | Three researchers search the web at the same time: competitors, prices people pay, demand signals. Every claim has a link | The evidence contradicts a make-or-break assumption |
| 4. Leadership review | Reads everything and writes a memo of at most 300 words | (never kills; it recommends) |
| 5. Final call | Reads only the memo and answers GO or NO-GO | (gives the verdict) |

## Good to know

- **Not made by Anthropic.** This is an independent, free project.
- **A second opinion, not professional advice.** The verdict comes from an AI review of what you wrote. Use it to think, not as financial, legal or business advice.
- **Strict, not rigged.** It doesn't need to kill a quota of ideas, and it's told never to invent a flaw.

## For developers

- `skills/go-no-go/SKILL.md` holds the judgment: the gate, the five round briefs, and how they run (separate sub-agents in Claude Code, so no round sees your chat).
- `skills/go-no-go/scripts/gonogo.py` does the deterministic parts: gate check, slug, idea hash, ledger, saving the record. Python standard library only.
- Tests: `python3 -m unittest discover tests`
- Check the plugin: `claude plugin validate .`

MIT licence.
