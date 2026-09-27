"""Which comments did this change leave wrong? Compare two versions of a Java file, method by method.

    uv run python prototype/examples/stale_comments/check_change.py OLD.java NEW.java
    git show HEAD~1:src/Foo.java > /tmp/old.java && uv run python .../check_change.py /tmp/old.java src/Foo.java

For every method whose code changed while its Javadoc did not (methods with a body; overloaded names are skipped), each part of the Javadoc (the summary sentence, each
@param line, the @return line, the units stale_comment.yml was measured on) is judged with the spec. Exits 1 when a
part is stale and sure (route `act`), so it can fail a CI step or a pre-commit hook; parts it is unsure of are listed
for a person.
"""
import re
import sys
from pathlib import Path

from rows import change

import hunch

SPEC = Path(__file__).parent / "stale_comment.yml"
METHOD = re.compile(r"(/\*\*(?:(?!\*/).)*\*/)\s*((?:@\w+(?:\([^)]*\))?\s*)*[\w<>\[\],.?\s]+?\s(\w+)\s*\([^)]*\)[^{;]*)\{", re.S)


def body_end(src: str, i: int) -> int:
    """The index of the brace that closes the one at `i`, skipping braces in strings, characters and comments."""
    depth, n = 0, len(src)
    while i < n:
        c = src[i]
        if src.startswith("//", i):
            i = src.find("\n", i) % (n + 1)
        elif src.startswith("/*", i):
            i = src.find("*/", i + 2) % (n + 1) + 1
        elif c in "\"'":
            i += 1
            while i < n and src[i] != c:
                i += 2 if src[i] == "\\" else 1
        elif c in "{}":
            depth += 1 if c == "{" else -1
            if depth == 0:
                return i
        i += 1
    return n - 1


def methods(src: str) -> tuple[dict, set]:
    """({name: (javadoc, code)} for methods with a Javadoc and a body, {overloaded names}). Methods without a body
    (interfaces, abstract) are not found, and an overloaded name is left out: two versions can't be paired by name."""
    found, seen = {}, set()
    for m in METHOD.finditer(src):
        name = m.group(3)
        if name in seen:
            found.pop(name, None)
            continue
        seen.add(name)
        found[name] = (m.group(1), src[m.start(2): body_end(src, m.end() - 1) + 1])
    return found, seen - set(found)


def parts(javadoc: str) -> list:
    """The summary sentence, each @param line and the @return line of a Javadoc."""
    text = re.sub(r"^\s*/?\*+/?", "", re.sub(r"\s*\*/\s*$", "", javadoc), flags=re.M).strip()
    body, *tags = re.split(r"(?:^|\n)\s*(?=@)", text)  # a Javadoc can start with a tag: then body is empty
    summary = re.split(r"(?<=\.)\s", " ".join(body.split()), maxsplit=1)[0]
    return [summary] * bool(summary) + [" ".join(t.split()) for t in tags if t.startswith(("@param", "@return"))]


def main(old_path: str, new_path: str) -> int:
    (old, over_old), (new, over_new) = methods(Path(old_path).read_text()), methods(Path(new_path).read_text())
    for name in sorted(over_old | over_new):
        print(f"SKIP   {name}: overloaded, not checked")
    stale = 0
    for name, (doc, code) in new.items():
        if name not in old or old[name][0] != doc or old[name][1] == code:
            continue  # new method, comment edited too, or code unchanged
        facts = change(old[name][1], code)
        for part in parts(doc):
            a = hunch.judge(SPEC, id=f"{name}:{part[:40]}", comment=part, signature_change=facts,
                            old_code=old[name][1], new_code=code)["stale"]
            if a["label"] == "yes":
                sure = a["route"] == "act"
                stale += sure
                print(f"{'STALE ' if sure else 'CHECK '} {name}: {part}   (p={a['p']:.2f}; {facts})")
    return 1 if stale else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
