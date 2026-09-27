"""Candidate pairs for same_product.yml: for each Abt product, the five Buy products most likely to be it.

Comparing every Abt product with every Buy product is 1,081 × 1,092 = 1.18M pairs. Two cheap signals narrow that
down in SQL before anything is asked: a shared model code (`PSLX350H` in both, hyphens ignored), then the share of
name words the two have in common. The five best per Abt product keep 1,061 of the 1,097 true matches.

    source: py(candidates.py:pairs)      # in the spec
"""
from pathlib import Path

import duckdb

HERE = Path(__file__).parent
TOP = 5

SQL = f"""
-- the words of a text: lower case, letters and digits only ("PS-LX350H Belt-Drive" -> ps, lx350h, belt, drive)
create macro words(text) as list_distinct(list_filter(
    string_split_regex(lower(regexp_replace(text, '[^A-Za-z0-9]+', ' ', 'g')), ' '), w -> length(w) > 1));

-- the model codes in a text: words with letters and digits, 4+ characters, after dropping - / .
-- ("Sony PS-LX350H Belt-Drive Turntable" -> PSLX350H)
create macro codes(text) as list_distinct(list_filter(
    string_split_regex(upper(regexp_replace(text, '[-/.]', '', 'g')), '[^A-Z0-9]+'),
    w -> length(w) >= 4 and regexp_matches(w, '[0-9]') and regexp_matches(w, '[A-Z]')));

-- do two texts share a model code? equal, or one inside the other (PSLX350H in PSLX350HBLK)
create macro shares_code(a, b) as len(list_filter(codes(a), u -> len(list_filter(codes(b), v ->
    u = v or (length(u) >= 5 and length(v) >= 5 and (contains(u, v) or contains(v, u))))) > 0)) > 0;

-- how many words two texts share, and that as a share of all their words
create macro shared_words(a, b) as len(list_intersect(words(a), words(b)));
create macro word_overlap(a, b) as shared_words(a, b)::double / len(list_distinct(list_concat(words(a), words(b))));

create table abt as select * from read_csv('{HERE}/abt.csv', all_varchar = true);
create table buy as
    select * replace (coalesce(description, '') as description, coalesce(manufacturer, '') as manufacturer)
    from read_csv('{HERE}/buy.csv', all_varchar = true);
create table matches as select * from read_csv('{HERE}/matches.csv', all_varchar = true);

-- every pair of products that shares at least one word, with the two clues
create table scored as
with b as (
    select id,
           name || ' ' || manufacturer as words_text,
           name || ' ' || description  as codes_text
    from buy
)
select abt.id as abt_id,
       b.id   as buy_id,
       shares_code(abt.name, b.codes_text)  as code,
       word_overlap(abt.name, b.words_text) as overlap
from abt, b
where shared_words(abt.name, b.words_text) > 0;
"""

PAIRS = f"""
select c.abt_id || '~' || c.buy_id as id, c.abt_id, c.buy_id, c.rank::varchar as rank,
       a.name as abt_name, coalesce(a.description, '') as abt_description, coalesce(a.price, '') as abt_price,
       b.name as buy_name, coalesce(b.manufacturer, '') as buy_manufacturer,
       coalesce(b.description, '') as buy_description, coalesce(b.price, '') as buy_price,
       case when m.idAbt is null then 'no' else 'yes' end as gold_same
from (select *, row_number() over (partition by abt_id order by code desc, overlap desc, buy_id) as rank
      from scored) c
join abt a on a.id = c.abt_id
join buy b on b.id = c.buy_id
left join matches m on m.idAbt = c.abt_id and m.idBuy = c.buy_id
where c.rank <= {TOP}
order by c.abt_id, c.rank
"""


def connect():
    con = duckdb.connect()
    con.execute(SQL)
    return con


def pairs():
    con = connect()
    cur = con.execute(PAIRS)
    cols = [d[0] for d in cur.description]
    for row in cur.fetchall():
        yield dict(zip(cols, row))


if __name__ == "__main__":
    rows = list(pairs())
    kept = sum(r["gold_same"] == "yes" for r in rows)
    print(f"{len(rows)} candidate pairs; {kept} of 1097 true matches among them")
