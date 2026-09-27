"""`hunch test` metrics under `weights:` describe the population, not the sample."""
import json

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


def test_rows_that_weigh_nothing_do_not_crash_the_metric(hunch):  # a population share of 0
    (hunch.dir / "rows.csv").write_text("id,text,grp\n1,a,x\n2,b,y\n3,c,y\n")
    (hunch.dir / "spec.yml").write_text(SPEC.replace("x: 0.5, y: 0.5", "x: 0, y: 1") + "where: grp == 'x'\n")
    hunch("test")
