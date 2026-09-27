"""`hunch run` on awkward data: rows it can't compare, and filters that keep nothing."""
import sqlite3

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
