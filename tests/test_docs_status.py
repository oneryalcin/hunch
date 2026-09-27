from hunch import docs

SPEC = {
    "spec_hash": "same",
    "questions": {"flag": {"type": "noul", "instructions": "Flag it?"}},
    "metrics": {},
}


def report(*, assessment=None, checks=None, accuracy=True, unavailable=None, spec_hash="same"):
    q = {"type": "noul", "rows": 8, "checks": checks or []}
    if accuracy:
        q["accuracy"] = {"value": 0.75, "basis": "gold", "gold": {"rows": 8}}
    if unavailable:
        q["unavailable_checks"] = unavailable
    out = {"spec_hash": spec_hash, "questions": {"flag": q}}
    if assessment:
        out["assessment"] = assessment
    return out


def test_docs_use_report_assessment_when_there_are_no_failures():
    assert docs.status(SPEC, report(assessment="passed")) == "ok"
    assert docs.status(SPEC, report(assessment="measured")) == "measured"
    assert docs.status(SPEC, report(assessment="no_gold", accuracy=False)) == "nogold"
    unassessed = report(assessment="unassessed", accuracy=False, unavailable=["min_accuracy"])
    assert docs.status(SPEC, unassessed) == "unassessed"


def test_docs_preserve_failure_warning_stale_and_missing_result_precedence():
    failed = [{"check": "min_accuracy", "passed": False, "severity": "error"}]
    warned = [{"check": "min_accuracy", "passed": False, "severity": "warn"}]

    assert docs.status(SPEC, report(assessment="passed", checks=failed)) == "fail"
    assert docs.status(SPEC, report(assessment="passed", checks=warned)) == "warn"
    assert docs.status(SPEC, report(assessment="passed", spec_hash="old")) == "stale"
    assert docs.status(SPEC, None) == "noresults"


def test_old_results_need_configured_checks_before_they_are_passing():
    old_check = [{"check": "min_accuracy", "passed": True, "value": 0.75, "limit": 0.7}]
    measured = report(checks=old_check)
    gated_spec = SPEC | {"tests": {"flag": {"min_accuracy": 0.7}}}

    assert docs.status(SPEC, measured) == "measured"
    assert docs.status(gated_spec, measured) == "ok"


def test_old_results_do_not_pass_when_a_configured_gate_was_not_recorded():
    implicit = [{"check": "min_accuracy", "passed": True, "value": 0.75, "limit": 0}]
    gated_spec = SPEC | {"tests": {"flag": {"min_auroc": 0.8}}}
    assert docs.status(gated_spec, report(checks=implicit)) == "unassessed"

    accuracy_gate = SPEC | {"tests": {"flag": {"min_accuracy": 0.7}}}
    assert docs.status(accuracy_gate, report(checks=implicit)) == "unassessed"


def test_old_results_with_unavailable_configured_checks_are_not_assessed():
    gated_spec = SPEC | {"tests": {"flag": {"min_accuracy": 0.7}}}
    unassessed = report(accuracy=False, unavailable=["min_accuracy"])

    assert docs.status(gated_spec, unassessed) == "unassessed"


def test_status_summary_words_distinguish_measured_passing_and_unassessed():
    measured_html = docs.status_summary(SPEC, report(assessment="measured"), "measured", None, [], None, [])
    passing_html = docs.status_summary(SPEC, report(assessment="passed"), "ok", None, [], None, [])
    unassessed_html = docs.status_summary(
        SPEC,
        report(assessment="unassessed", accuracy=False, unavailable=["min_accuracy"]),
        "unassessed",
        None,
        [],
        None,
        [],
    )

    assert "Measured, with no configured acceptance checks" in measured_html
    assert "Passing configured checks" in passing_html
    assert "Configured checks could not run" in unassessed_html


def test_new_statuses_keep_existing_visual_classes():
    assert 'class="pill st-measured st-ok"' in docs.pill("measured")
    assert 'class="pill st-unassessed st-nogold"' in docs.pill("unassessed")
