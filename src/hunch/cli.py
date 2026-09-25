"""The `hunch` command: `init` (install a recipe) here, every other command in hunch.core."""
import shutil
import sys
from pathlib import Path

RECIPES = Path(__file__).parent / "recipes"


def init(argv: list[str]) -> None:
    """hunch init RECIPE [DIR]: copy a recipe (specs, README, tests) into DIR to adapt. hunch init --list."""
    names = sorted(p.name.replace("_", "-") for p in RECIPES.iterdir() if (p / "README.md").exists())
    if not argv or argv[0] in ("--list", "-l"):
        for n in names:
            first = (RECIPES / n.replace("-", "_") / "README.md").read_text().splitlines()
            print(f"{n:<16} {next((l for l in first[1:] if l.strip()), '')}")
        return
    name = argv[0].replace("-", "_")
    if not (RECIPES / name).is_dir():
        sys.exit(f"no recipe {argv[0]!r}; have {names}")
    dest = Path(argv[1] if len(argv) > 1 else name)
    if dest.exists():
        sys.exit(f"{dest} exists; pick another folder: hunch init {argv[0]} <DIR>")
    shutil.copytree(RECIPES / name, dest, ignore=shutil.ignore_patterns("__pycache__"))
    print(f"{argv[0]} → {dest}/  next: point the source at your rows, then `hunch compile {dest}` (cost), "
          f"`hunch run {dest}`, `hunch review {dest}` (build gold), `hunch test {dest}`")


SKILL = Path(__file__).parent / "skills" / "hunch" / "SKILL.md"
SKILL_DIRS = (".claude/skills", ".agents/skills")  # Claude Code; Codex and Cursor (Cursor reads both)


def skill(argv: list[str]) -> None:
    """hunch skill [DIR]: install the agent skill into DIR (default: here); re-run after upgrading hunch."""
    if argv and argv[0].startswith("-"):
        print("usage: hunch skill [DIR]   (installs .claude/skills/hunch and .agents/skills/hunch under DIR)", file=sys.stderr)
        sys.exit(2)
    root, text = Path(argv[0] if argv else "."), SKILL.read_text()
    for d in SKILL_DIRS:
        dest = root / d / "hunch" / "SKILL.md"
        state = "up to date" if dest.exists() and dest.read_text() == text else "updated" if dest.exists() else "installed"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)
        print(f"{dest}  {state}")


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "init":
        return init(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "skill":
        return skill(sys.argv[2:])
    from hunch.core import main as core_main
    core_main()


if __name__ == "__main__":
    main()
