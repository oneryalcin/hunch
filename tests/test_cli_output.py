"""The default CLI tells a reader what happened without changing the evaluation."""

import json
import sys
from pathlib import Path

import pytest

from hunch import cli
from hunch.commands import main
from hunch.measure import summary_markdown

SPEC = """judgment: urgent
model: fake:big
source: rows.csv
key: id
state: [text]
questions:
  urgent: {type: noul, instructions: "Is it urgent?", gold: gold_urgent}
"""


@pytest.fixture(autouse=True)
def spec(hunch):
    (hunch.dir / "rows.csv").write_text("id,text,gold_urgent\n1,a,yes\n2,b,no\n3,c,yes\n")
    (hunch.dir / "spec.yml").write_text(SPEC)


def result(hunch):
    return json.loads((hunch.dir / ".hunch" / "target" / "spec.json").read_text())


def test_run_leads_with_rows_answers_cost_and_inspection(hunch, capsys):
    hunch("run")
    out = capsys.readouterr().out
    assert "Run complete" in out
    assert "3 rows in" in out and "3 written" in out
    assert "0 cached, 3 asked" in out
    assert "urgent: yes 3" in out
    assert "Inspect: hunch show" in out
    assert "Measure: hunch test" in out


def test_ungated_measurement_is_not_called_passing(hunch, capsys):
    hunch("test")
    out = " ".join(capsys.readouterr().out.split())
    doc = result(hunch)
    assert "no acceptance checks configured" in out
    assert "2/3 agree with gold" in out
    assert "1 disagreement" in out
    assert "PASS accuracy" not in out and "(min 0%)" not in out
    assert doc["assessment"] == doc["judgments"]["urgent"]["assessment"] == "measured"
    assert doc["passed"] is True and doc["judgments"]["urgent"]["questions"]["urgent"]["checks"] == []


def test_configured_failing_check_controls_exit_and_report(hunch, capsys):
    (hunch.dir / "spec.yml").write_text(SPEC + "tests:\n  urgent: {min_accuracy: 0.8}\n")
    with pytest.raises(SystemExit) as e:
        hunch("test")
    assert e.value.code == 1
    out = capsys.readouterr().out
    assert "Configured check failed" in out
    assert "FAIL min_accuracy" in out
    assert result(hunch)["assessment"] == "failed"


def test_unavailable_check_is_distinct_from_no_gold(hunch, capsys):
    (hunch.dir / "rows.csv").write_text("id,text\n1,a\n2,b\n")
    (hunch.dir / "spec.yml").write_text(SPEC + "tests:\n  urgent: {min_accuracy: 0.8}\n")
    hunch("test")
    out = capsys.readouterr().out
    assert "Some checks could not run" in out
    assert "NOT ASSESSED min_accuracy" in out
    assert result(hunch)["assessment"] == "unassessed"


def test_ci_summary_names_checks_that_could_not_run():
    doc = {"assessment": "unassessed", "passed": True, "sample": None, "judgments": {
        "urgent": {"questions": {"urgent": {
            "accuracy": {"value": 0.75, "ci": None},
            "checks": [{"check": "min_accuracy", "passed": True, "severity": "error"}],
            "unavailable_checks": ["min_auroc"],
        }}, "metrics": {"share": {"rate": 0.5, "missed": None, "checks": [],
                                 "unavailable_checks": ["max_missed"]}}}}}

    summary = summary_markdown(doc, Path("spec"))

    assert "NOT ASSESSED: min_auroc" in summary
    assert "NOT ASSESSED: max_missed" in summary
    assert "| urgent.urgent | 75.0% |  | passed |" not in summary


def test_weighted_accuracy_is_named_instead_of_turned_into_a_row_count(hunch, capsys):
    (hunch.dir / "rows.csv").write_text("id,text,gold_urgent,grp\n1,a,yes,x\n2,b,no,y\n3,c,yes,y\n")
    (hunch.dir / "spec.yml").write_text(SPEC.replace("questions:",
        "weights: {by: grp, population: {x: 0.5, y: 0.5}}\nquestions:"))
    hunch("test")
    out = capsys.readouterr().out
    q = result(hunch)["judgments"]["urgent"]["questions"]["urgent"]
    assert "weighted accuracy 75.0% on 3 gold rows" in out
    assert "2/3 agree" not in out
    assert q["weighted"] is True


def test_weighted_metric_distinguishes_sample_count_from_population_estimate(hunch, capsys):
    (hunch.dir / "rows.csv").write_text("id,text,gold_urgent,grp\n1,a,yes,x\n2,b,no,y\n3,c,yes,y\n")
    (hunch.dir / "spec.yml").write_text(SPEC.replace("questions:",
        "weights: {by: grp, population: {x: 0.5, y: 0.5}}\nmetrics:\n  share_x: {rule: \"grp == 'x'\"}\nquestions:"))
    hunch("test")
    out = " ".join(capsys.readouterr().out.split())
    assert "share_x: estimated population rate 50.0%" in out
    assert "95% CI" in out
    assert "1/3 sampled rows" in out
    assert "share_x: 1/3 rows (50.0%)" not in out


def test_docs_suggestion_omits_node_that_docs_does_not_accept(hunch, capsys):
    hunch("test", "--node", "urgent")
    out = capsys.readouterr().out
    assert "Browser report: hunch docs" in out
    assert "--node" not in out.split("Browser report: ")[1]


def test_verbose_keeps_the_same_results_and_cost(hunch, capsys):
    hunch("test")
    concise = result(hunch)
    capsys.readouterr()
    hunch("test", "--verbose")
    verbose = result(hunch)
    out = capsys.readouterr().out
    assert "stated p(yes)" in out and "dial" in out
    assert concise["judgments"] == verbose["judgments"]
    assert concise["cost"] == verbose["cost"] == 0


def test_show_works_through_cli_after_source_is_removed(hunch, capsys):
    hunch("run")
    (hunch.dir / "rows.csv").unlink()
    capsys.readouterr()
    hunch("show", "--id", "2")
    out = capsys.readouterr().out
    assert "gold_urgent: no" in out
    assert "urgent: yes" in out


def test_docs_open_is_opt_in(hunch, monkeypatch):
    opened = []
    monkeypatch.setattr("webbrowser.open", opened.append)
    hunch("test")
    hunch("docs")
    assert opened == []
    hunch("docs", "--open")
    assert len(opened) == 1 and opened[0].startswith("file:")


def test_html_uses_the_same_measured_status_as_the_cli(hunch, capsys):
    hunch("test")
    capsys.readouterr()
    hunch("docs")
    out = capsys.readouterr().out
    assert "1 measured" in out
    page = hunch.dir / ".hunch" / "target" / "spec.html"
    assert "Measured, with no configured acceptance checks" in page.read_text()


def test_diff_leads_with_changed_answers_and_keeps_diagnostics_optional(hunch, capsys):
    hunch("diff", "--model", "fake:small")
    concise = capsys.readouterr().out
    assert "Diff ·" in concise
    assert "3/3 shared rows changed answer" in concise
    assert "Gold on changed rows" in concise
    assert "probabilities moved" not in concise

    hunch("diff", "--model", "fake:small", "--verbose")
    verbose = capsys.readouterr().out
    assert "probabilities moved" in verbose


def test_command_help_is_specific(hunch, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["hunch", "show", "--help"])
    with pytest.raises(SystemExit) as e:
        main()
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "--id" in out and "--limit" in out
    assert "--receipt" not in out


def test_entrypoint_without_arguments_or_with_init_help_is_guiding(hunch, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["hunch"])
    cli.main()
    assert "Run, inspect, and measure" in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", ["hunch", "init", "--help"])
    cli.main()
    assert "Copy a starter recipe" in capsys.readouterr().out
