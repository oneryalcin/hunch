from types import SimpleNamespace

import pytest

from hunch.inspect import show
from hunch.spec import load_project

SPEC = """judgment: urgent
model: fake:big
source: rows.csv
key: ticket
state: [text]
questions:
  urgent: {type: noul, instructions: "Is it urgent?", act: 0.95}
"""


def args(**kw):
    base = {"node": None, "id": None, "limit": 20, "model": None, "target": None}
    base.update(kw)
    return SimpleNamespace(**base)


def project(hunch):
    return load_project(hunch.project)


@pytest.fixture(autouse=True)
def spec(hunch):
    (hunch.dir / "rows.csv").write_text("ticket,text\nr1,plain\nr2,other\n")
    (hunch.dir / "spec.yml").write_text(SPEC)


def test_show_does_not_create_a_store_when_none_exists(hunch):
    with pytest.raises(SystemExit) as e:
        show(project(hunch), args())

    assert "no store yet" in str(e.value)
    assert not (hunch.dir / ".hunch").exists()


def test_show_reads_the_materialized_table_without_the_source_csv(hunch, capsys):
    hunch("run")
    (hunch.dir / "rows.csv").unlink()
    capsys.readouterr()

    show(project(hunch), args())

    out = capsys.readouterr().out
    assert "urgent · last materialized run" in out
    assert "ticket" in out
    assert "r1" in out
    assert "yes 0.90 review" in out


def test_show_rejects_unknown_target_even_when_source_is_missing(hunch, capsys):
    hunch("run")
    (hunch.dir / "rows.csv").unlink()
    capsys.readouterr()

    with pytest.raises(SystemExit) as e:
        hunch("show", "--target", "typo")

    assert e.value.code == 2
    assert "--target typo: no judgment here defines it" in capsys.readouterr().err


def test_show_warns_when_the_table_was_written_by_an_older_spec(hunch, capsys):
    hunch("run")
    (hunch.dir / "spec.yml").write_text(SPEC.replace("Is it urgent?", "Is it very urgent?"))
    capsys.readouterr()

    show(project(hunch), args())

    assert "warning: table was written by spec" in capsys.readouterr().out


def test_sample_run_does_not_make_show_claim_it_wrote_a_new_table(hunch, capsys):
    hunch("run")
    old = capsys.readouterr().out
    (hunch.dir / "rows.csv").write_text("ticket,text\nr1,changed\nr2,changed too\n")
    hunch("run", "--sample", "1")
    sample = capsys.readouterr().out
    assert "tables were not replaced" in sample
    assert "Inspect:" not in sample

    show(project(hunch), args())
    out = capsys.readouterr().out
    assert "last materialized run" in out
    assert old.split("Run ID: ")[1].splitlines()[0] in out


def test_show_handles_an_empty_materialized_table(hunch, capsys):
    (hunch.dir / "rows.csv").write_text("ticket,text,year\nr1,plain,2001\n")
    (hunch.dir / "spec.yml").write_text(SPEC.replace("state: [text]", "state: [text]\nwhere: year > 2030"))
    hunch("run")
    capsys.readouterr()

    show(project(hunch), args())

    assert "no rows in this table" in capsys.readouterr().out


def test_show_id_prints_all_columns_with_safe_wrapping(hunch, capsys, monkeypatch):
    (hunch.dir / "rows.csv").write_text('ticket,text\nr1,"first line\nsecond\x01line with a very very long tail"\n')
    hunch("run")
    monkeypatch.setattr("hunch.inspect.shutil.get_terminal_size", lambda fallback: SimpleNamespace(columns=44))
    capsys.readouterr()

    show(project(hunch), args(id="r1"))

    out = capsys.readouterr().out
    assert "text: first line\\nsecond\\x01line with a" in out
    assert "very long tail" in out
    assert "\n      " in out
    assert "urgent_key:" in out


def test_show_reads_model_suffixed_tables(hunch, capsys):
    hunch("run", "--model", "fake:small")
    capsys.readouterr()

    show(project(hunch), args(model="fake:small"))

    out = capsys.readouterr().out
    assert "Table: urgent__fake_small" in out
    assert "Model: fake:small" in out
    assert "no 0.80 review" in out


def test_show_lists_union_rows_using_the_branches_key(hunch, capsys):
    hunch.project = hunch.dir
    (hunch.dir / "spec.yml").unlink()
    for name in ("first", "second"):
        which = "r1" if name == "first" else "r2"
        (hunch.dir / f"{name}.yml").write_text(SPEC.replace("judgment: urgent", f"judgment: {name}")
                                               + f"where: ticket == '{which}'\n")
    (hunch.dir / "tree.yml").write_text("judgment: tree\nunion: [first, second]\nquestion: urgent\n")
    hunch("run")
    capsys.readouterr()

    show(project(hunch), args(node="tree"))

    out = capsys.readouterr().out
    assert "tree · last materialized run" in out
    assert "ticket" in out and "_branch" in out
    assert "r1" in out and "first" in out and "second" in out
