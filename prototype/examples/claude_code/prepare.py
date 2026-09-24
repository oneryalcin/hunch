# /// script
# requires-python = ">=3.11"
# ///
"""Claude Code transcripts (session .jsonl files) → turns.csv, one row per human turn.

A turn: the human's request, what the agent did (tool counts, computed here: rules stay in code),
its final reply, and the human's next message (their reaction, the closest thing to free gold).

Public sessions (Trace Commons, CC BY 4.0: real developers, anonymized by each contributor, public repos):

    uvx --from huggingface_hub hf download trace-commons/agent-traces --repo-type dataset \
        --include "sessions/claude_code/*" --local-dir .cache/trace-commons
    uv run prepare.py --root .cache/trace-commons/sessions/claude_code

Your own (~/.claude/projects), only project folders whose name contains an --include pattern:

    uv run prepare.py --include dev-personal dev-games private-tmp

Secrets, emails and the home path are redacted before anything is written; turns.csv is gitignored.
"""
import argparse
import csv
import glob
import json
import os
import re
from pathlib import Path

HOME = os.path.expanduser("~")
REDACT = [  # (pattern, replacement), applied in order
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[PRIVATE_KEY]"),
    (re.compile(r"\b(sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_\w{20,}|xox[abpr]-[\w-]{10,}|AKIA[0-9A-Z]{16}|AIza[\w-]{30,})"), "[SECRET]"),
    (re.compile(r"(?i)\b(bearer)\s+[\w.~+/-]{16,}=*"), r"\1 [SECRET]"),
    (re.compile(r"(?i)\b([\w-]*(?:api[_-]?key|token|secret|password|passwd)[\w-]*)(\s*[=:]\s*)['\"]?[^\s'\"]{8,}"), r"\1\2[SECRET]"),
    (re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"), "[EMAIL]"),
    (re.compile(r"\b[A-Fa-f0-9]{32,}\b|\b[A-Za-z0-9+/_-]{48,}={0,2}"), "[BLOB]"),  # keys, hashes, tokens
    (re.compile(re.escape(HOME)), "~"),
]
CLIP = {"request": 3000, "final_reply": 3000, "next_message": 1500}


def redact(s: str) -> str:
    for pat, rep in REDACT:
        s = pat.sub(rep, s)
    return s


def clip(s: str, n: int, tail: bool = False) -> str:
    if len(s) <= n:
        return s
    return "…" + s[-n:] if tail else s[:n] + "…"


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content if b.get("type") == "text")


TAGGED = re.compile(r"<(ide_\w+|bash-\w+|command-\w+|local-command-\w+|system-reminder|task-notification|"
                    r"teammate-message|user-prompt-submit-hook)[^>]*>.*?</\1>", re.S)  # injected by the harness
INTERRUPT = "[Request interrupted by user"


def human_text(r: dict) -> str | None:
    """What a person typed, or None. `origin` exists only in newer Claude Code versions (2.1 has none), so the
    fallback is: a user message that isn't a tool result, meta, a sidechain, or wholly harness-injected tags."""
    if r.get("type") != "user" or r.get("isSidechain") or r.get("isMeta"):
        return None
    if (r.get("origin") or {}).get("kind") not in (None, "human"):
        return None
    content = r["message"]["content"]
    if isinstance(content, list) and any(b.get("type") == "tool_result" for b in content):
        return None
    t = TAGGED.sub("", text_of(content)).strip()
    if not t or t.startswith(("<", "/", "This session is being continued")):
        return None
    return t


def turns(path: str) -> list[dict]:
    recs = []
    for line in open(path):
        try:
            recs.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # a session being written right now can end mid-line
    session = Path(path).stem
    out, cur = [], None
    for r in recs:
        text = human_text(r)
        if text is not None:
            if cur:
                cur["next_message"] = text
                out.append(cur)
            cur = {"request": text, "final_reply": "", "tools": 0, "edits": 0, "ran_after_edit": False,
                   "at": r.get("timestamp", ""), "project": Path(path).parent.name}
        elif cur and r.get("type") == "assistant" and not r.get("isSidechain"):
            for b in r["message"].get("content") or []:
                if b.get("type") == "text" and b["text"].strip():
                    cur["final_reply"] = b["text"].strip()  # last text before the next human message
                elif b.get("type") == "tool_use":
                    cur["tools"] += 1
                    if b["name"] in ("Edit", "Write", "NotebookEdit", "MultiEdit"):
                        cur["edits"] += 1
                        cur["ran_after_edit"] = False
                    elif b["name"] == "Bash" and cur["edits"]:
                        cur["ran_after_edit"] = True
    # the last turn has no reaction yet: dropped; an interrupt is a reaction, not a request
    return [{"id": f"{session[:8]}#{i}", **t} for i, t in enumerate(out)
            if t["final_reply"] and not t["request"].startswith(INTERRUPT)]


def main() -> None:
    p = argparse.ArgumentParser()
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--root", type=Path, help="read every .jsonl under this folder")
    src.add_argument("--include", nargs="+", help="~/.claude/projects folder name substrings to read")
    p.add_argument("--out", default=Path(__file__).with_name("turns.csv"))
    args = p.parse_args()
    if args.root:
        files = sorted(glob.glob(str(args.root / "**/*.jsonl"), recursive=True))
    else:
        files = sorted(f for f in glob.glob(os.path.join(HOME, ".claude/projects/*/*.jsonl"))
                       if any(k in Path(f).parent.name for k in args.include))
    rows = [t for f in files for t in turns(f)]
    for r in rows:
        r["request"] = clip(redact(r["request"]), CLIP["request"])
        r["final_reply"] = clip(redact(r["final_reply"]), CLIP["final_reply"], tail=True)  # claims come last
        r["next_message"] = clip(redact(r["next_message"]), CLIP["next_message"])
        r["project"] = redact(r["project"].replace(HOME.replace("/", "-"), "~"))
        r["ran_after_edit"] = "yes" if r["ran_after_edit"] else ("no" if r["edits"] else "")
    cols = ["id", "project", "at", "request", "final_reply", "next_message", "tools", "edits", "ran_after_edit"]
    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, cols)
        w.writeheader()
        w.writerows({k: r[k] for k in cols} for r in rows)
    print(f"{len(files)} sessions → {len(rows)} turns → {args.out}")


if __name__ == "__main__":
    main()
