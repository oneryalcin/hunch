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
