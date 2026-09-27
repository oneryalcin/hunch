"""--target: a dev run must never touch the production store, reuse another engine's answers or be misread."""
import json
import sqlite3

import pytest

from hunch import core

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
    with_targets = core.load_spec(hunch.dir / "spec.yml")
    without = core.load_spec(hunch.dir / "spec.yml", SPEC.split("targets:")[0])
    assert core.spec_hash(with_targets) == core.spec_hash(without)
