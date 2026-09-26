"""hunch in dbt: a spec's decisions as SQL functions in dbt-duckdb models.

In profiles.yml, under the DuckDB output:

    plugins:
      - module: hunch.dbt
        config:
          specs: [hunch/command_guard.yml]   # paths from where dbt runs; each becomes a function (see hunch.sql)
          max_cost: 1.00                     # USD for the whole dbt invocation, across every function

A model then selects `command_guard(request, cwd, description, command).destroys.label`, and the decision is a
column dbt builds, tests and documents like any other. Needs dbt-duckdb and the `sql` extra.
"""
from dbt.adapters.duckdb.plugins import BasePlugin

from hunch import sql


class Plugin(BasePlugin):
    def initialize(self, config: dict) -> None:
        self.specs = config.get("specs") or []
        self.budget = sql.Budget(config.get("max_cost"))  # one budget for every connection dbt opens

    def configure_connection(self, conn) -> None:
        for spec in self.specs:
            sql.register(conn, spec, max_cost=self.budget)
