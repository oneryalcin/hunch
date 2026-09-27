"""A spec written in the wrong shape gets a message naming the shape, not a traceback; unions of unions work."""
import json

import pytest

SPEC = """judgment: x
model: fake:big
source: rows.csv
key: id
state: [text]
where: grp == 'x'
questions:
  urgent: {type: noul, instructions: "Is it urgent?"}
"""


def branches(hunch, *names):
    (hunch.dir / "rows.csv").write_text("id,text,grp\n1,a,x\n2,b,y\n3,c,z\n")
    hunch.project = hunch.dir
    for b in names:
        (hunch.dir / f"{b}.yml").write_text(SPEC.replace("judgment: x", f"judgment: {b}").replace("'x'", f"'{b}'"))


def test_a_union_of_unions_is_tested(hunch):  # lint read a union branch's `questions`: KeyError
    branches(hunch, "x", "y", "z")
    (hunch.dir / "u.yml").write_text("judgment: u\nunion: [x, y]\nquestion: urgent\n")
    (hunch.dir / "uu.yml").write_text("judgment: uu\nunion: [u, z]\nquestion: urgent\n"
                                      "metrics:\n  m: {rule: \"urgent == 'yes'\"}\n")
    hunch("test")
    m = json.loads(next((hunch.dir / ".hunch" / "target").glob("*.json")).read_text())["judgments"]["uu"]["metrics"]["m"]
    assert (m["fired"], m["rows"]) == (3, 3)


def test_a_union_key_not_every_branch_has_is_a_lint_error(hunch, capsys):  # passed lint unchecked
    branches(hunch, "x", "y")
    (hunch.dir / "u.yml").write_text("judgment: u\nunion: [x, y]\nquestion: urgent\nkey: nope\n")
    with pytest.raises(SystemExit):
        hunch("lint")
    assert "key 'nope' is not a column every branch has" in capsys.readouterr().err


@pytest.mark.parametrize("old, new, says", [
    ("questions:\n  urgent: {type: noul, instructions: \"Is it urgent?\"}", "questions: [urgent]", "questions: maps"),
    ("where: grp == 'x'", "where: 1", "where: is text"),
    ("source: rows.csv", "source: [rows.csv]", "source: is text"),
    ("model: fake:big", "model: 1.13", "model: is text"),
    ("judgment: x\n", "", "judgment: is text"),
])
def test_a_spec_in_the_wrong_shape_is_named_at_load(hunch, old, new, says):  # AttributeError / TypeError / KeyError
    (hunch.dir / "rows.csv").write_text("id,text,grp\n1,a,x\n")
    (hunch.dir / "spec.yml").write_text(SPEC.replace(old, new))
    with pytest.raises(SystemExit) as e:
        hunch("lint")
    assert says in str(e.value.code)


@pytest.mark.parametrize("union", ["[]", "xy"])
def test_a_union_in_the_wrong_shape_is_named_at_load(hunch, union):  # [] passed lint; 'xy' read as ['x', 'y']
    branches(hunch, "x", "y")
    (hunch.dir / "u.yml").write_text(f"judgment: u\nunion: {union}\nquestion: urgent\n")
    with pytest.raises(SystemExit) as e:
        hunch("lint")
    assert "union: lists the judgments it combines" in str(e.value.code)


@pytest.mark.parametrize("key, value", [("clip", "[text]"), ("redact", "1")])
def test_clip_or_redact_in_the_wrong_shape_is_a_lint_error(hunch, capsys, key, value):  # .items() / iter crashed lint
    (hunch.dir / "rows.csv").write_text("id,text,grp\n1,a,x\n")
    (hunch.dir / "spec.yml").write_text(SPEC + f"{key}: {value}\n")
    with pytest.raises(SystemExit):
        hunch("lint")
    assert f"{key}: " in capsys.readouterr().err


def test_an_outer_union_asks_only_what_an_inner_union_carries(hunch, capsys):  # lint passed; u's rows dropped
    branches(hunch, "x", "y", "z")
    for b in ("x", "y", "z"):  # every leaf asks both; u carries only urgent
        f = hunch.dir / f"{b}.yml"
        f.write_text(f.read_text() + "  severity: {type: noul, instructions: \"Is it severe?\"}\n")
    (hunch.dir / "u.yml").write_text("judgment: u\nunion: [x, y]\nquestion: urgent\n")
    (hunch.dir / "uu.yml").write_text("judgment: uu\nunion: [u, z]\nquestion: severity\n")
    with pytest.raises(SystemExit):
        hunch("lint")
    assert "branch 'u' has no question 'severity'" in capsys.readouterr().err


def test_an_upstream_without_a_key_is_a_lint_error(hunch, capsys):  # KeyError in topo_project
    branches(hunch, "x")
    (hunch.dir / "x.yml").write_text((hunch.dir / "x.yml").read_text().replace("key: id\n", ""))
    (hunch.dir / "u.yml").write_text("judgment: u\nunion: [x]\nquestion: urgent\n")
    with pytest.raises(SystemExit):
        hunch("lint")
    assert "missing key" in capsys.readouterr().err
