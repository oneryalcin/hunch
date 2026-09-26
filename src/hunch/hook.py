"""hunch as a coding agent's before-command hook.

`hunch hook install` copies the command guard into hunch/command_guard.yml (yours to edit and commit) and adds one
PreToolUse hook for shell commands to .claude/settings.json and/or .codex/hooks.json. `hunch hook run SPEC` is
what the agent calls: it judges the command with the spec, keeps the row (log=True, so `hunch review` and
`hunch test --traffic` work on real use), and holds the command for a person when any answer is yes, or when an
answer is below its act bar. Otherwise it says nothing and the agent's own permission rules apply: the hook never
approves a command, so it can't loosen anyone's permissions. Any error lets the command through with a warning:
a broken guard must not stop the agent.
"""
import json
import shutil
import sys
from pathlib import Path

RECIPE = Path(__file__).parent / "recipes" / "agent_commands"
SPEC = Path("hunch") / "command_guard.yml"
# Where each agent reads project hooks, and the command it runs (hooks run from the session's folder; point at the
# project's copy of the spec wherever that is).
# `|| echo`: a hunch that is missing or too old to know `hook` must not block (Claude Code blocks on exit 2), and
# must not fail silently either.
UNCHECKED = """ || echo '{"systemMessage": "hunch command guard did not run: is hunch 0.3 or later on PATH?"}'"""
AGENTS = {
    "claude": (Path(".claude") / "settings.json",
               'hunch hook run "$CLAUDE_PROJECT_DIR/hunch/command_guard.yml"' + UNCHECKED),
    "codex": (Path(".codex") / "hooks.json",  # Codex can't ask before a tool runs yet: it denies, with the reason
              'hunch hook run --deny "$(git rev-parse --show-toplevel)/hunch/command_guard.yml"' + UNCHECKED),
}
TIMEOUT = 30  # seconds; a cached answer takes milliseconds, a new one about 0.3 s
DEFAULT_MAX_COST = 0.01  # USD per command; one command costs about $0.00003 on Jev


def install(root: Path, agents: list[str]) -> list[str]:
    """Copy the guard (its spec, and the 38 labelled commands it was measured on, so `hunch test hunch/` works from
    the start; files already there are kept) and add the hook to each agent's project settings, keeping every other
    setting. Nothing is written unless every settings file could be read. Running it again changes nothing."""
    done, writes = [], []
    for a in agents:
        rel, command = AGENTS[a]
        path = root / rel
        text = path.read_text() if path.exists() else ""
        try:
            cfg = json.loads(text) if text.strip() else {}
            groups = cfg.setdefault("hooks", {}).setdefault("PreToolUse", [])
            ours = any("hunch hook run" in h.get("command", "") for g in groups for h in g.get("hooks", []))
        except (ValueError, AttributeError, TypeError) as e:
            sys.exit(f"{rel}: can't read or merge it ({e}); nothing was changed. Add this PreToolUse hook by hand:\n"
                     + json.dumps({"matcher": "Bash", "hooks": [{"type": "command", "command": command, "timeout": TIMEOUT}]}))
        if ours:
            done.append(f"{rel}  already runs the guard")
            continue
        groups.append({"matcher": "Bash", "hooks": [{"type": "command", "command": command, "timeout": TIMEOUT}]})
        writes.append((path, cfg))
        done.append(f"{rel}  runs the guard before every shell command")
    copied = []
    for f in ("command_guard.yml", "commands.csv", "NOTICE.md"):
        dest = root / SPEC.parent / f
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(RECIPE / f, dest)
            copied.append(f)
    if copied:
        done.insert(0, f"{SPEC.parent}/  the guard's questions and 38 labelled commands: edit them, commit them")
    for path, cfg in writes:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cfg, indent=2) + "\n")
    ignore = root / ".gitignore"  # the store keeps every judged command: never commit it
    lines = ignore.read_text().splitlines() if ignore.exists() else []
    if not {".hunch", ".hunch/", "/.hunch", "/.hunch/"} & {line.strip() for line in lines}:
        ignore.write_text("\n".join([*lines, ".hunch/"]) + "\n")
        done.append(".gitignore  .hunch/ (the store keeps the commands it judged: redacted, but yours)")
    return done


def request_of(transcript: str | None) -> str:
    """The last message the person typed, read with hunch's own trace reader (the rows the guard was measured on
    came from the same reader). Empty when there is no transcript or its format is unknown."""
    if not transcript or not Path(transcript).is_file():
        return ""
    from hunch import traces
    try:
        events = [e for _, ev in traces.read(transcript) for e in ev]
    except Exception:  # an odd transcript costs the request, never the check
        return ""
    return next((e["text"] for e in reversed(events) if e["role"] == "human"), "")


def hold_reasons(answers: dict) -> list[str]:
    """Why a person should look: every yes, and every answer hunch isn't sure of."""
    out = []
    for q, a in answers.items():
        if a["label"] == "yes":
            out.append(f"{q} (p={a['p']:.2f})")
        elif a["route"] == "review":
            out.append(f"{q}: unsure (p={a['p']:.2f} {a['label']})")
    return out


def decide(event: dict, spec: str, deny: bool = False) -> dict | None:
    """The hook's reply for one PreToolUse event, or None to leave the command to the agent's permission rules."""
    from hunch import core
    if core.MAX_COST is None:  # a hook runs unattended: cap it unless HUNCH_MAX_COST says otherwise
        core.MAX_COST = DEFAULT_MAX_COST
    tool_input = event.get("tool_input") or {}
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if event.get("tool_name") != "Bash" or not isinstance(command, str):
        return None
    row = {"id": event.get("tool_use_id") or "", "request": request_of(event.get("transcript_path")),
           "cwd": event.get("cwd") or "", "description": str(tool_input.get("description") or ""), "command": command}
    answers = core.judge(spec, log=True, **row)
    reasons = hold_reasons(answers)
    if not reasons:
        return None
    why = "hunch command guard: " + ", ".join(reasons)
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny" if deny else "ask",
        "permissionDecisionReason": why + (". Ask the person to confirm, then have them run it or approve it."
                                           if deny else "")}}


def run(spec: str, deny: bool = False) -> None:
    try:
        reply = decide(json.load(sys.stdin), spec, deny)
    except (Exception, SystemExit) as e:  # SystemExit: hunch's own refusals (cost cap, missing key, lint)
        print(json.dumps({"systemMessage": f"hunch command guard did not run, so the command was not checked: {e}"}))
        return
    if reply:
        print(json.dumps(reply))


USAGE = """usage: hunch hook install [--agent claude|codex|both] [DIR]   add the command guard to a project
       hunch hook run [--deny] SPEC                               (what the agent calls: reads the event on stdin)"""


def main(argv: list[str]) -> None:
    cmd, args = (argv[0], argv[1:]) if argv else ("", [])
    flags = {a for a in args if a.startswith("--")}
    agent = next((args[i + 1] for i, a in enumerate(args[:-1]) if a == "--agent"), None)
    rest = [a for i, a in enumerate(args) if not a.startswith("--") and not (i and args[i - 1] == "--agent")]
    if cmd == "run" and len(rest) == 1 and flags <= {"--deny"}:
        return run(rest[0], deny="--deny" in flags)
    if cmd == "install" and len(rest) <= 1 and flags <= {"--agent"} and agent in (None, "claude", "codex", "both"):
        agents = [agent] if agent in ("claude", "codex") else ["claude", "codex"]
        for line in install(Path(rest[0] if rest else "."), agents):
            print(line)
        print("next: make sure `hunch` (0.3 or later) is on PATH for the agent (uv tool install hunch-ai) and "
              "TYPESAFE_API_KEY is set; Codex asks you to trust new hooks under /hooks. Decisions are kept: "
              f"`hunch review {SPEC} --traffic`, `hunch test {SPEC} --traffic`")
        return
    print(USAGE, file=sys.stderr)
    sys.exit(2)
