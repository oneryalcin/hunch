"""The rows of comments.csv, plus what the commit changed in the method's signature, found by code.

Whether a parameter was renamed or a return type changed is a fact a parser gets exactly; whether a sentence of
documentation still holds is a judgment. This source hands the model the facts, so the question is only the judgment:

    source: py(rows.py:rows)     # in the spec

`signature_change` reads like "parameter colProj (ColumnReadProjection) removed; parameter columnSchema
(ColumnMetadata) added", or "none". `param_rule` is the plain rule for @param comments alone: `yes` when the
parameter the comment names is gone or has a new type, else `no`; empty for other comments.
"""
import csv
import re
from pathlib import Path

HERE = Path(__file__).parent


def signature(code: str):
    """(return type, [(type, name)]) of a Java method, or None when the header can't be read."""
    head = code[: code.find("{")] if "{" in code else code
    head = re.sub(r"@\w+(\([^)]*\))?", " ", head)  # annotations
    m = re.search(r"([\w\[\]<>?,.\s]+?)\s*\((.*)\)", head, re.S)
    if not m:
        return None
    words = re.sub(r"<[^<>]*>", "", m.group(1)).split()  # drop generics, then the words before the name
    ret = words[-2] if len(words) >= 2 else ""
    if ret in {"public", "private", "protected", "static", "final", "synchronized", "abstract", "native"}:
        ret = ""  # a constructor
    params = []
    for a in re.sub(r"<[^<>]*>", "", m.group(2)).split(","):
        parts = [p for p in a.split() if p != "final"]
        if len(parts) >= 2:
            params.append((" ".join(parts[:-1]), parts[-1]))
    return ret, params


def change(old: str, new: str) -> str:
    a, b = signature(old), signature(new)
    if a is None or b is None:
        return "unknown"
    out = []
    if a[0] != b[0]:
        out.append(f"return type {a[0] or 'none'} → {b[0] or 'none'}")
    ta, tb = {n: t for t, n in a[1]}, {n: t for t, n in b[1]}
    out += [f"parameter {n} ({t}) removed" for n, t in ta.items() if n not in tb]
    out += [f"parameter {n} ({t}) added" for n, t in tb.items() if n not in ta]
    out += [f"parameter {n} type {ta[n]} → {tb[n]}" for n in ta if n in tb and ta[n] != tb[n]]
    return "; ".join(out) or "none"


def param_rule(comment: str, old: str, new: str) -> str:
    words = comment.split()
    a, b = signature(old), signature(new)
    if len(words) < 2 or a is None or b is None:
        return ""
    ta, tb = {n: t for t, n in a[1]}, {n: t for t, n in b[1]}
    return "yes" if words[1] not in tb or ta.get(words[1]) != tb[words[1]] else "no"


def rows():
    for r in csv.DictReader(open(HERE / "comments.csv")):
        r["signature_change"] = change(r["old_code"], r["new_code"])
        r["param_rule"] = param_rule(r["comment"], r["old_code"], r["new_code"]) if r["kind"] == "param" else ""
        yield r


if __name__ == "__main__":
    rs = list(rows())
    checked = [r for r in rs if r["kind"] == "param" and r["gold_checked"]]
    right = sum(r["param_rule"] == r["gold_checked"] for r in checked)
    print(f"{len(rs)} rows; signature unreadable on {sum(r['signature_change'] == 'unknown' for r in rs)}; "
          f"param rule right on {right} of {len(checked)} hand-checked @param comments")
    for r in rs[:4]:
        print(r["kind"], "|", r["comment"][:60], "|", r["signature_change"])
