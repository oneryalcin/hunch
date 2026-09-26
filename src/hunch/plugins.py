"""`hunch plugins` and `hunch install`: which engine plugins hunch can see, and adding one where it can see it.

hunch is usually installed as a tool (`uv tool install hunch-ai`), in an environment of its own, so a plain
`pip install hunch-engine-foo` lands where hunch never looks. `hunch install` puts a package in hunch's own
environment: in a uv tool install it rebuilds the tool with the package added (uv has no in-place add, and a
package installed beside the tool would be dropped by the next `uv tool upgrade`); anywhere else it installs into
the interpreter hunch runs on. Plugin packages are named `hunch-engine-<name>` on PyPI.
"""
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

from hunch import core, engines

USAGE = """usage: hunch plugins                     the engines hunch can use: built in, and from installed plugins
       hunch install PACKAGE [PACKAGE…]    install engine plugins into hunch's own environment (hunch-engine-<name>)"""


def listing() -> list[str]:
    loaded = engines.installed()
    out = [f"built in: jev-…, distilled:<folder>, {', '.join(p + ':<model>' for p in core.ENDPOINTS)}"]
    if not loaded and not engines.shadowed:
        return out + ["from plugins: none installed (hunch install hunch-engine-<name>)"]
    out.append("from plugins:")
    for prefix, e in sorted(loaded.items()):
        pkg = engines._package.get(prefix, "?")
        out.append(f"  {prefix + ':':<14} {pkg:<32} " + (f"failed to load: {e!r}" if isinstance(e, Exception) else "ok"))
    out += [f"  ignored: {s}, its prefix is built in" for s in engines.shadowed]
    return out


def tool_receipt() -> Path | None:
    """uv's record of how this tool environment was built, when hunch runs from `uv tool install`."""
    p = Path(sys.prefix) / "uv-receipt.toml"
    return p if p.exists() else None


def spec_of(req: dict) -> str | None:
    """A requirement from uv's receipt as a command-line spec; None for one a spec can't say (git, a path, a URL)."""
    if any(k in req for k in ("git", "path", "url", "directory", "editable")):
        return None
    return req["name"] + (f"[{','.join(req['extras'])}]" if req.get("extras") else "") + (req.get("specifier") or "")


def install_command(packages: list[str]) -> tuple[list[str] | None, str]:
    """(the command to run, or None if it must be run by hand, and a note)."""
    receipt = tool_receipt()
    if receipt:
        reqs = tomllib.loads(receipt.read_text())["tool"]["requirements"]
        specs = [spec_of(r) for r in reqs]
        if None in specs or not reqs:
            return None, (f"hunch is a uv tool built from a source a command line can't restate ({receipt}); "
                          f"add the plugin with: uv tool install <how you installed hunch> "
                          + " ".join(f"--with {s}" for s in (*[s for s in specs[1:] if s], *packages)))
        keep = [s for s in specs[1:] if s.split("[")[0].split("=")[0].split(">")[0].split("<")[0] not in packages]
        cmd = ["uv", "tool", "install", specs[0], *[x for s in (*keep, *packages) for x in ("--with", s)]]
        return cmd, "rebuilds hunch's uv tool environment with the plugin added"
    if shutil.which("uv"):
        return ["uv", "pip", "install", "--python", sys.executable, *packages], "into the environment hunch runs in"
    return [sys.executable, "-m", "pip", "install", *packages], "into the environment hunch runs in"


def main(argv: list[str]) -> None:
    cmd, args = (argv[0], argv[1:]) if argv else ("", [])
    if cmd == "plugins" and not args:
        print("\n".join(listing()))
        return
    if cmd == "install" and args and not any(a.startswith("-") for a in args):
        run, note = install_command(args)
        if run is None:
            sys.exit(note)
        print(f"$ {' '.join(run)}   ({note})", flush=True)
        if subprocess.run(run).returncode:
            sys.exit(1)
        print("check it with: hunch plugins")
        return
    print(USAGE, file=sys.stderr)
    sys.exit(2)
