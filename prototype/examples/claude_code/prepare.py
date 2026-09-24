# /// script
# requires-python = ">=3.11"
# ///
"""Claude Code transcripts (~/.claude/projects/*/*.jsonl) → turns.csv, one row per human turn.

A turn: the human's request, what the agent did (tool counts, computed here: rules stay in code),
its final reply, and the human's next message (their reaction, the closest thing to free gold).

Only projects whose folder name contains an --include pattern are read. Secrets, emails and the home
path are redacted here, before anything is written; turns.csv is gitignored and never committed.

    uv run prepare.py --include dev-personal dev-games private-tmp
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


def is_human(r: dict) -> bool:
    if r.get("type") != "user" or r.get("isSidechain") or r.get("isMeta"):
        return False
    if (r.get("origin") or {}).get("kind") != "human":
        return False
    t = text_of(r["message"]["content"]).strip()
    return bool(t) and not t.startswith(("<command-", "<local-command", "/"))


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
        if is_human(r):
            text = text_of(r["message"]["content"]).strip()
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
    # the last turn has no reaction yet: dropped
    return [{"id": f"{session[:8]}#{i}", **t} for i, t in enumerate(out) if t["final_reply"]]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--include", nargs="+", required=True, help="project folder name substrings to read")
    p.add_argument("--out", default=Path(__file__).with_name("turns.csv"))
    args = p.parse_args()
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
