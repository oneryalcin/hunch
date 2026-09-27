"""--target: a dev run must never touch the production store, reuse another engine's answers or be misread."""
import json
import sqlite3

import pytest

from hunch.spec import load_spec
from hunch.store import spec_hash

SPEC = """judgment: urgent
model: fake:big
source: rows.csv
key: id
state: [text]
questions:
  urgent: {type: noul, instructions: "Is it urgent?"}
targets:
  dev: {model: fake:small, store: dev.sqlite}
"""


@pytest.fixture(autouse=True)
def spec(hunch):
    (hunch.dir / "spec.yml").write_text(SPEC)


def tables(path):
    return {r[0] for r in sqlite3.connect(path).execute("select name from sqlite_master where type = 'table'")}


def test_dev_run_writes_only_its_own_store_even_with_hunch_store_set(hunch, monkeypatch):
    prod = hunch.dir / "prod.sqlite"
    monkeypatch.setenv("HUNCH_STORE", str(prod))
    hunch("run", "--target", "dev")
    assert not prod.exists() and "urgent@dev" in tables(hunch.dir / "dev.sqlite")


def test_show_reads_the_target_table_and_store(hunch, capsys):
    hunch("run", "--target", "dev")
    capsys.readouterr()
    hunch("show", "--target", "dev")
    out = capsys.readouterr().out
    assert "Table: urgent@dev" in out
    assert "Store: " in out and "dev.sqlite" in out
    assert "Model: fake:small" in out


def test_answers_cached_for_one_engine_are_not_reused_for_the_target_engine(hunch):
    hunch("run")
    (hunch.dir / "spec.yml").write_text(SPEC.replace("store: dev.sqlite", "sample: 3"))  # same store as prod
    hunch.fake.asked.clear()
    hunch("test", "--target", "dev")
    assert hunch.fake.asked == ["fake:small"] * 3


def test_a_target_engine_in_the_prod_store_leaves_the_specs_own_table_alone(hunch):
    hunch("run")
    (hunch.dir / "spec.yml").write_text(SPEC.replace("store: dev.sqlite", "max_cost: 1"))
    hunch("run", "--target", "dev")
    db = sqlite3.connect(hunch.dir / ".hunch" / "store.sqlite")
    assert db.execute("select distinct urgent from urgent").fetchall() == [("yes",)]


def test_a_downstream_judgment_without_a_target_keeps_its_production_table(hunch):
    (hunch.dir / "spec.yml").write_text(SPEC.replace("store: dev.sqlite", "max_cost: 1"))
    (hunch.dir / "down.yml").write_text("judgment: down\nmodel: fake:big\nsource: ref(urgent)\nstate: [text]\n"
                                        "questions:\n  angry: {type: noul, instructions: \"Angry?\"}\n")
    hunch.project = hunch.dir  # the folder: both specs
    hunch("run")
    hunch("run", "--target", "dev")
    db = sqlite3.connect(hunch.dir / ".hunch" / "store.sqlite")
    assert db.execute("select distinct urgent from down").fetchall() == [("yes",)]


def test_model_flag_beats_the_target_model(hunch):
    hunch("test", "--target", "dev", "--model", "fake:big")
    assert set(hunch.fake.asked) == {"fake:big"}


def test_target_results_do_not_replace_the_default_results(hunch):
    (hunch.dir / "spec.yml").write_text(SPEC.replace("model: fake:small, store: dev.sqlite", "sample: 2"))
    hunch("test")
    hunch("test", "--target", "dev")
    default = json.loads((hunch.dir / ".hunch" / "target" / "spec.json").read_text())
    assert (default["target"], default["sample"]) == (None, None)


def test_a_mistyped_target_key_is_an_error(hunch):
    (hunch.dir / "spec.yml").write_text(SPEC.replace("store:", "stor:"))
    with pytest.raises(SystemExit):
        hunch("test", "--target", "dev")
    assert hunch.fake.asked == []


def test_an_unknown_target_is_an_error(hunch):
    with pytest.raises(SystemExit):
        hunch("test", "--target", "prod")
    assert hunch.fake.asked == []


def test_adding_targets_does_not_change_the_spec_hash(hunch):
    with_targets = load_spec(hunch.dir / "spec.yml")
    without = load_spec(hunch.dir / "spec.yml", SPEC.split("targets:")[0])
    assert spec_hash(with_targets) == spec_hash(without)


def test_judge_with_a_target_answers_with_its_model_into_its_store(hunch):  # else an app's dev calls hit prod
    from hunch.online import judge
    assert judge(hunch.dir / "spec.yml", target="dev", text="a")["urgent"]["label"] == "no"  # fake:small says 0.2
    assert (hunch.dir / "dev.sqlite").exists() and not (hunch.dir / ".hunch" / "store.sqlite").exists()


def test_hunch_target_picks_the_target_for_judge(hunch, monkeypatch):  # how a server or app selects dev
    from hunch.online import judge
    monkeypatch.setenv("HUNCH_TARGET", "dev")
    judge(hunch.dir / "spec.yml", text="b")
    assert hunch.fake.asked == ["fake:small"]
