"""Agent traces → rows for hunch. Stdlib only.

Every format is read into one event stream per session, then viewed as rows:

    event: {"session", "role": "human" | "agent", "text", "tools": [names], "at"}

    turns: one row per human message: the request, the agent's final reply, the human's next message
           (their reaction: the closest thing to free gold), what the agent did in between.
    runs:  one row per session: the first request, the agent's final messages, totals.

Formats (detected from the file): Claude Code session .jsonl, Cursor agent .jsonl, OpenCode session .json,
OpenTelemetry GenAI spans (`gen_ai.input.messages` / `gen_ai.output.messages`, one trace per line or a
list of spans; Langfuse and other OTel-based tools export this).

In a spec:  source: traces(sessions/**/*.jsonl)     # view: turns (default) or runs
"""
import glob
import json
import re
from pathlib import Path

# Tool names differ by harness; these two families are what the views count.
EDIT_TOOLS = {"edit", "write", "multiedit", "notebookedit", "strreplace", "str_replace", "apply_patch", "create_file"}
RUN_TOOLS = {"bash", "shell", "run_terminal_cmd", "exec_command", "execute", "terminal", "run"}
# Text the harness injects into a human message (IDE context, `!` shell I/O, reminders, Cursor's wrappers).
INJECTED = re.compile(r"<(ide_\w+|bash-\w+|command-\w+|local-command-\w+|system-reminder|task-notification|"
                      r"teammate-message|cross-session-message|user-prompt-submit-hook|manually_attached_skills|attached_files)[^>]*>.*?</\1>", re.S)
# Whole messages the harness delivers as the user's turn: another session's message, a system notification.
NOT_HUMAN = ("Another Claude session sent a message", "A peer session sent a message", "[SYSTEM NOTIFICATION - NOT USER INPUT]")
UNWRAP = re.compile(r"</?user_query>")
INTERRUPT = "[Request interrupted by user"
TURN_COLUMNS = ["id", "session", "at", "request", "final_reply", "next_message", "tools", "edits", "ran_after_edit"]
RUN_COLUMNS = ["id", "session", "at", "request", "final_messages", "human_messages", "tools", "edits", "ran_after_edit"]


def human(text: str) -> str | None:
    t = UNWRAP.sub("", INJECTED.sub("", text)).strip()
    if not t or t.startswith(("<", "/", "This session is being continued") + NOT_HUMAN):
        return None
    return t


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text") or b.get("content") or "" for b in content
                     if isinstance(b, dict) and b.get("type") == "text")


# ---------- readers: file → [(session, [events])] ----------

def claude_code(path: Path, lines: list[dict]) -> list[tuple[str, list[dict]]]:
    ev = []
    for r in lines:
        if r.get("isSidechain") or r.get("isMeta"):
            continue
        m, t = r.get("message") or {}, r.get("type")
        content = m.get("content")
        if t == "user" and (r.get("origin") or {}).get("kind") in (None, "human"):
            if isinstance(content, list) and any(b.get("type") == "tool_result" for b in content):
                continue
            h = human(text_of(content))
            if h:
                ev.append({"role": "human", "text": h, "tools": [], "at": r.get("timestamp", "")})
        elif t == "assistant":
            blocks = content if isinstance(content, list) else [{"type": "text", "text": content or ""}]
            ev.append({"role": "agent", "text": text_of(blocks).strip(), "at": r.get("timestamp", ""),
                       "tools": [b["name"] for b in blocks if b.get("type") == "tool_use"]})
    return [(path.stem, ev)]


def cursor(path: Path, lines: list[dict]) -> list[tuple[str, list[dict]]]:
    ev = []
    for r in lines:
        blocks = (r.get("message") or {}).get("content") or []
        if r.get("role") == "user":
            h = human(text_of(blocks))
            if h:
                ev.append({"role": "human", "text": h, "tools": [], "at": ""})
        elif r.get("role") == "assistant":
            ev.append({"role": "agent", "text": text_of(blocks).replace("[REDACTED]", "").strip(), "at": "",
                       "tools": [b["name"] for b in blocks if b.get("type") == "tool_use"]})
    return [(path.stem, ev)]


def opencode(path: Path, doc: dict) -> list[tuple[str, list[dict]]]:
    ev = []
    for m in doc.get("messages", []):
        parts, role = m.get("parts", []), m["info"]["role"]
        text = "\n".join(p.get("text", "") for p in parts if p.get("type") == "text").strip()
        at = str((m["info"].get("time") or {}).get("created", ""))
        if role == "user":
            h = human(text)
            if h:
                ev.append({"role": "human", "text": h, "tools": [], "at": at})
        else:
            ev.append({"role": "agent", "text": text, "at": at,
                       "tools": [p.get("tool", "") for p in parts if p.get("type") == "tool"]})
    return [(doc["info"]["id"], ev)]


def otel(path: Path, traces: list[dict]) -> list[tuple[str, list[dict]]]:
    """GenAI spans carry the conversation so far (input) and the reply (output). The latest span of a trace
    has the whole history, so it alone is read; tool calls are `tool_call` parts."""
    out = []
    for tr in traces:
        spans = [s for s in tr.get("spans", [tr]) if "gen_ai.input.messages" in s.get("attributes", {})]
        if not spans:
            continue
        last = max(spans, key=lambda s: (len(s["attributes"]["gen_ai.input.messages"]), s.get("start_time", "")))
        a = last["attributes"]
        msgs = json.loads(a["gen_ai.input.messages"]) + json.loads(a.get("gen_ai.output.messages") or "[]")
        ev = []
        for m in msgs:
            parts = m.get("parts", [])
            text = "\n".join(p.get("content", "") for p in parts if p.get("type") == "text").strip()
            if m["role"] == "user":
                h = human(text)
                if h:
                    ev.append({"role": "human", "text": h, "tools": [], "at": ""})
            elif m["role"] == "assistant":
                ev.append({"role": "agent", "text": text, "at": "",
                           "tools": [p.get("name", "") for p in parts if p.get("type") == "tool_call"]})
        out.append((tr.get("trace_id") or last.get("trace_id", path.stem), ev))
    return out


def read(path: str | Path) -> list[tuple[str, list[dict]]]:
    path = Path(path)
    raw = path.read_text()
    if path.suffix == ".json":
        doc = json.loads(raw)
        if isinstance(doc, dict) and "info" in doc and "messages" in doc:
            return opencode(path, doc)
        return otel(path, doc if isinstance(doc, list) else [doc])
    lines = []
    for line in raw.splitlines():
        try:
            lines.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # a session still being written can end mid-line
    first = next((r for r in lines if isinstance(r, dict)), {})
    if "spans" in first:
        return otel(path, lines)
    if "attributes" in first:  # one span per line (OTLP file exporters): group them into traces first
        traces: dict[str, list] = {}
        for s in lines:
            traces.setdefault(s.get("trace_id") or s.get("traceId") or "", []).append(s)
        return otel(path, [{"trace_id": tid, "spans": spans} for tid, spans in traces.items()])
    if "sessionId" in first or any("sessionId" in r for r in lines[:20]):
        return claude_code(path, lines)
    if "role" in first or any("role" in r for r in lines[:20]):
        return cursor(path, lines)
    raise ValueError(f"{path}: unrecognised trace format")


# ---------- views: events → rows ----------

def _did(agent_events: list[dict]) -> dict:
    tools = [t.lower() for e in agent_events for t in e["tools"]]
    edits, ran = 0, False
    for t in tools:
        if t in EDIT_TOOLS:
            edits, ran = edits + 1, False
        elif t in RUN_TOOLS and edits:
            ran = True
    return {"tools": len(tools), "edits": edits, "ran_after_edit": ("yes" if ran else "no") if edits else ""}


def turns(session: str, ev: list[dict]) -> list[dict]:
    """Ids number the human messages the reader keeps, so a change in what counts as human shifts later ids in that
    session. Reviews follow their text (core.attach_gold), so they survive the shift."""
    rows, cur, agent, n = [], None, [], 0
    for e in ev:
        if e["role"] == "human":
            if cur is not None:
                final = next((a["text"] for a in reversed(agent) if a["text"]), "")
                if final and not cur["text"].startswith(INTERRUPT):  # an interruption is a reaction, not a request
                    rows.append({"id": f"{session[:8]}#{n}", "session": session, "at": cur["at"],
                                 "request": cur["text"], "final_reply": final, "next_message": e["text"], **_did(agent)})
                n += 1
            cur, agent = e, []
        elif cur is not None:
            agent.append(e)
    return rows  # the last turn has no reaction yet


def runs(session: str, ev: list[dict]) -> list[dict]:
    hum = [e for e in ev if e["role"] == "human"]
    agent = [e for e in ev if e["role"] == "agent"]
    if not hum or not agent:
        return []
    tail = [a["text"] for a in agent if a["text"]][-3:]
    return [{"id": session[:8], "session": session, "at": hum[0]["at"], "request": hum[0]["text"],
             "final_messages": "\n---\n".join(tail), "human_messages": len(hum), **_did(agent)}]


VIEWS = {"turns": (turns, TURN_COLUMNS), "runs": (runs, RUN_COLUMNS)}


def rows(pattern: str, base: Path = Path("."), view: str = "turns") -> list[dict]:
    fn, _ = VIEWS[view]
    files = sorted(glob.glob(str(base / Path(pattern).expanduser()), recursive=True))
    if not files:
        raise FileNotFoundError(f"traces({pattern}): no files under {base}")
    return [r for f in files for s, ev in read(f) for r in fn(s, ev)]


if __name__ == "__main__":  # quick look: python traces.py 'glob' [turns|runs]
    import sys
    rs = rows(sys.argv[1], view=sys.argv[2] if len(sys.argv) > 2 else "turns")
    print(len(rs), "rows")
    for r in rs[:3]:
        print({k: (v[:80] if isinstance(v, str) else v) for k, v in r.items()})
