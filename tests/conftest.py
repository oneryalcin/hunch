"""A free, in-process engine and a `hunch` command runner in a throwaway project folder."""
import sys

import pytest

from hunch import engines, settings, store
from hunch.commands import main


class Fake:  # a free, local engine: p(yes) depends on the model, so answers say which engine gave them
    adapter = "fake-1"

    def __init__(self):
        self.asked = []

    async def answer(self, model, state, questions):
        self.asked.append(model)
        return {"answers": {k: {"type": "noul", "noul": 0.9 if model == "fake:big" else 0.2} for k in questions}}


@pytest.fixture
def hunch(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()  # the default store goes to the repo root: this folder
    (tmp_path / "rows.csv").write_text("id,text\n1,a\n2,b\n3,c\n")
    fake = Fake()
    monkeypatch.setattr(engines, "_loaded", {"fake": fake})
    monkeypatch.setattr(settings, "MAX_COST", None)
    monkeypatch.setattr(store, "_conns", {})
    monkeypatch.delenv("HUNCH_STORE", raising=False)

    def run(*argv):
        monkeypatch.setattr(sys, "argv", ["hunch", argv[0], str(run.project), *argv[1:]])
        try:
            main()
        except SystemExit as e:
            if e.code:
                raise
    run.dir, run.fake, run.project = tmp_path, fake, tmp_path / "spec.yml"
    return run
