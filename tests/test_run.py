"""`hunch run` on awkward data: rows it can't compare, filters that keep nothing, an empty file."""
import json
import sqlite3

import pytest

SPEC = """judgment: recent
model: fake:big
source: rows.csv
key: id
state: [text]
where: year > 2008
questions:
  recent: {type: noul, instructions: "Recent?"}
"""


def output(hunch, table):
    return sqlite3.connect(hunch.dir / ".hunch" / "store.sqlite").execute(f'select id from "{table}"').fetchall()


def test_a_non_numeric_cell_is_filtered_out_not_a_crash(hunch):
    (hunch.dir / "rows.csv").write_text("id,text,year\n1,a,2010\n2,b,n/a\n3,c,2001\n")
    (hunch.dir / "spec.yml").write_text(SPEC)
    hunch("run")
    assert output(hunch, "recent") == [("1",)]


def test_a_filter_that_keeps_nothing_writes_an_empty_table_downstream_reads(hunch):
    (hunch.dir / "rows.csv").write_text("id,text,year\n1,a,2001\n")
    (hunch.dir / "spec.yml").write_text(SPEC)
    (hunch.dir / "down.yml").write_text("judgment: down\nmodel: fake:big\nsource: ref(recent)\nkey: id\n"
                                        "state: [text]\nquestions:\n  old: {type: noul, instructions: \"Old?\"}\n")
    hunch.project = hunch.dir
    hunch("run")
    cols = [r[1] for r in sqlite3.connect(hunch.dir / ".hunch" / "store.sqlite").execute("pragma table_info(down)")]
    assert output(hunch, "down") == [] and {"year", "recent", "old", "old_key"} <= set(cols)


def test_in_reads_a_cell_as_a_number_when_the_list_holds_numbers(hunch):  # else it keeps nothing, silently
    (hunch.dir / "rows.csv").write_text("id,text,year\n1,a,2008\n2,b,2010\n3,c,2009.0\n")
    (hunch.dir / "spec.yml").write_text(SPEC.replace("year > 2008", "year in [2008, 2009]"))
    hunch("run")
    assert output(hunch, "recent") == [("1",), ("3",)]


def test_a_bare_column_reading_false_or_0_drops_the_row(hunch):  # else `where: flag` keeps 'false'
    (hunch.dir / "rows.csv").write_text("id,text,year\n1,a,true\n2,b,False\n3,c,0\n4,d, \n")
    (hunch.dir / "spec.yml").write_text(SPEC.replace("year > 2008", "year"))
    hunch("run")
    assert output(hunch, "recent") == [("1",)]


def test_a_number_in_text_matches_nothing_not_a_crash(hunch):  # `in <string>` needs text on the left
    (hunch.dir / "rows.csv").write_text("id,text,year\n1,a 2.5,2010\n")
    (hunch.dir / "spec.yml").write_text(SPEC.replace("year > 2008", "2.5 in text or 2.5 not in text"))
    hunch("run")
    assert output(hunch, "recent") == []


def test_a_bare_yes_no_answer_in_where_is_a_lint_warning(hunch, capsys):  # it keeps the 'no' rows too
    (hunch.dir / "rows.csv").write_text("id,text,year\n1,a,2010\n")
    (hunch.dir / "spec.yml").write_text(SPEC)
    (hunch.dir / "down.yml").write_text("judgment: down\nmodel: fake:big\nsource: ref(recent)\nkey: id\nstate: [text]\n"
                                        "where: recent\nquestions:\n  old: {type: noul, instructions: \"Old?\"}\n")
    hunch.project = hunch.dir
    hunch("lint")
    assert "down: where: 'recent' on its own holds for 'no' too" in capsys.readouterr().err


def test_an_empty_csv_is_a_lint_error_not_a_traceback(hunch, capsys):
    (hunch.dir / "rows.csv").write_text("")
    (hunch.dir / "spec.yml").write_text(SPEC)
    with pytest.raises(SystemExit):
        hunch("lint")
    assert "rows.csv is empty" in capsys.readouterr().err


def test_a_union_whose_branches_keep_nothing_is_tested_with_0_rows(hunch):
    (hunch.dir / "rows.csv").write_text("id,text,year\n1,a,2001\n")
    hunch.project = hunch.dir
    for b in ("recent", "later"):
        (hunch.dir / f"{b}.yml").write_text(SPEC.replace("judgment: recent", f"judgment: {b}"))
    (hunch.dir / "tree.yml").write_text("judgment: tree\nunion: [recent, later]\nquestion: recent\n")
    hunch("test")
    report = json.loads(next((hunch.dir / ".hunch" / "target").glob("*.json")).read_text())
    assert report["judgments"]["tree"]["questions"]["recent"]["rows"] == 0
