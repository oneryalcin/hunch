"""`hunch test` metrics under `weights:` describe the population, not the sample."""
import json

import pytest

SPEC = """judgment: urgent
model: fake:big
source: rows.csv
key: id
state: [text]
weights: {by: grp, population: {x: 0.5, y: 0.5}}
questions:
  urgent: {type: noul, instructions: "Is it urgent?"}
metrics:
  first: {rule: "text == 'a'"}
"""


def test_the_overall_metric_rate_is_weighted(hunch):
    (hunch.dir / "rows.csv").write_text("id,text,grp\n1,a,x\n2,b,y\n3,c,y\n")
    (hunch.dir / "spec.yml").write_text(SPEC)
    hunch("test")
    m = json.loads((hunch.dir / ".hunch" / "target" / "spec.json").read_text())["judgments"]["urgent"]["metrics"]["first"]
    assert (m["fired"], m["rows"], m["rate"]) == (1, 3, 0.5)  # 1 of 3 sampled rows, half the population


def test_a_population_share_of_0_is_a_lint_error_not_a_crash_in_test(hunch, capsys):  # all-0 weights divide by 0
    (hunch.dir / "rows.csv").write_text("id,text,grp\n1,a,x\n2,b,y\n3,c,y\n")
    (hunch.dir / "spec.yml").write_text(SPEC.replace("x: 0.5, y: 0.5", "x: 0, y: 1"))
    with pytest.raises(SystemExit):
        hunch("test")
    assert "shares must be numbers above 0" in capsys.readouterr().err



def test_a_metric_on_a_union_reads_its_one_question(hunch):  # a union has no `questions`: KeyError
    (hunch.dir / "rows.csv").write_text("id,text,grp\n1,a,x\n2,b,y\n")
    hunch.project = hunch.dir
    for b in ("x", "y"):
        (hunch.dir / f"{b}.yml").write_text(SPEC.replace("judgment: urgent", f"judgment: {b}")
                                            .replace("weights: {by: grp, population: {x: 0.5, y: 0.5}}", f"where: grp == '{b}'"))
    (hunch.dir / "u.yml").write_text("judgment: u\nunion: [x, y]\nquestion: urgent\nmetrics:\n  m: {rule: \"urgent == 'yes'\"}\n")
    hunch("test")
    m = json.loads(next((hunch.dir / ".hunch" / "target").glob("*.json")).read_text())["judgments"]["u"]["metrics"]["m"]
    assert (m["fired"], m["rows"]) == (2, 2)


def test_a_population_list_is_a_lint_error_not_a_traceback(hunch, capsys):  # lint called .values() on it
    (hunch.dir / "rows.csv").write_text("id,text,grp\n1,a,x\n2,b,y\n")
    (hunch.dir / "spec.yml").write_text(SPEC.replace("{x: 0.5, y: 0.5}", "[x, y]"))
    with pytest.raises(SystemExit):
        hunch("lint")
    assert "weights.population maps each value" in capsys.readouterr().err
