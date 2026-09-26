"""Which comments did this change leave wrong? Compare two versions of a Java file, method by method.

    uv run python prototype/examples/stale_comments/check_change.py OLD.java NEW.java
    git show HEAD~1:src/Foo.java > /tmp/old.java && uv run python .../check_change.py /tmp/old.java src/Foo.java

For every method whose code changed while its Javadoc did not, each part of the Javadoc (the summary sentence, each
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


def methods(src: str) -> dict:
    """{name: (javadoc, code)} for methods with a Javadoc, code from the header to the matching brace."""
    out = {}
    for m in METHOD.finditer(src):
        depth, i = 0, m.end() - 1
        for i in range(m.end() - 1, len(src)):
            depth += {"{": 1, "}": -1}.get(src[i], 0)
            if depth == 0:
                break
        out.setdefault(m.group(3), (m.group(1), src[m.start(2): i + 1]))  # overloads: the first one only
    return out


def parts(javadoc: str) -> list:
    """The summary sentence, each @param line and the @return line of a Javadoc."""
    text = re.sub(r"^\s*/?\*+/?", "", javadoc, flags=re.M).strip()
    body, *tags = re.split(r"\n\s*(?=@)", text)
    summary = re.split(r"(?<=\.)\s", " ".join(body.split()), maxsplit=1)[0]
    out = [summary] if summary and not summary.startswith("@") else []
    return out + [" ".join(t.split()) for t in tags if t.startswith(("@param", "@return"))]


def main(old_path: str, new_path: str) -> int:
    old, new = methods(Path(old_path).read_text()), methods(Path(new_path).read_text())
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
