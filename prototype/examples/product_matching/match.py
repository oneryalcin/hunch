"""Match Abt's catalogue to Buy's, end to end in DuckDB: candidate pairs, a decision on each, one match per product.

    uv run --extra sql python prototype/examples/product_matching/match.py     # about $0.14 the first time, then free

Prints how the matches score against the benchmark's answer key, beside the rule alone (each Abt product's best
candidate by model code and name words, no model).
"""
import os
from pathlib import Path

import candidates

import hunch.sql

HERE = Path(__file__).parent

con = candidates.connect()
hunch.sql.register(con, HERE / "same_product.yml", max_cost=float(os.environ.get("HUNCH_MAX_COST", 0.25)))
con.execute(f"create table pairs as {candidates.PAIRS}")

con.execute("""
create table decided as
select abt_id, buy_id, rank, d.same.label as label, d.same.p as p, d.same.route as route,
       case when d.same.label = 'yes' then d.same.p else 1 - d.same.p end as p_yes
from (select *, same_product(abt_name, abt_description, abt_price, buy_name, buy_manufacturer,
                             buy_description, buy_price) as d
      from pairs)
""")

# One match per product on each side: a pair is kept when the model says yes and it is the likeliest pair for both
# its Abt product and its Buy product. Probabilities tie often, so ties go to the better candidate rank, then the id.
con.execute("""
create table matched as
select * from (select * from decided where label = 'yes'
               qualify row_number() over (partition by abt_id order by p_yes desc, rank::int, buy_id) = 1)
qualify row_number() over (partition by buy_id order by p_yes desc, rank::int, abt_id) = 1
""")
con.execute("create table rule as select abt_id, buy_id from pairs where rank = '1'")


def score(table, where="true"):
    n, right = con.execute(f"""
        select count(*), count(m.idAbt) from {table} t
        left join matches m on m.idAbt = t.abt_id and m.idBuy = t.buy_id where {where}""").fetchone()
    p, r = right / n, right / 1097
    return f"{n:>5} matches, {right:>5} right: precision {p:.1%}, recall {r:.1%}, F1 {2 * p * r / (p + r):.1%}"


print("rule alone (best candidate by code and words)  ", score("rule"))
print("hunch: decision on each pair, one per product  ", score("matched"))
print("hunch, only matches it is sure of (route act)  ", score("matched", "route = 'act'"))
routes = con.execute("select route, count(*) from decided group by 1 order by 1").fetchall()
print("pairs by route:", dict(routes))
assert con.execute("select count(*) = count(distinct abt_id) and count(*) = count(distinct buy_id) from matched").fetchone()[0]
