"""Engines from other packages: a decision model hunch doesn't ship, used like the ones it does.

A package registers an engine under the entry-point group `hunch.engines`; the entry point's name is the prefix
of the spec's `model`:

    # the plugin's pyproject.toml
    [project.entry-points."hunch.engines"]
    ollama = "hunch_engine_ollama:engine"          # model: ollama:<model name>

The engine is any object with an async `answer`, called once per row with every question for that row:

    async def answer(model: str, state: dict | str, questions: dict[str, dict]) -> dict
        questions: {id: question as hunch sends it to Jev: type, instructions, criteria}
        returns {"answers": {id: answer}, "cost": USD charged (default 0), "tokens": input tokens (default 0)}

Answers take Jev's shapes, so everything downstream (act, review, test, diff, distill, SQL) works unchanged:

    noul    {"type": "noul", "noul": p_yes}
    choice  {"type": "choice", "choice": label, "confidence": p, "probabilities": {label: p, …}}
    score   {"type": "score", "score": expected_level, "confidence": p, "legend": {"0": text, …},
             "probabilities": {"0": p, …}}

and, optionally:

    key: str          an environment variable it needs; hunch checks it before asking
    worst_cost(model, state, questions) -> USD
                      the most one call can be charged, for --max-cost (without it: 0, a local engine)
    adapter: str      how it asks, as part of every answer's cache key: change it when the engine's prompt or
                      parsing changes, so old answers aren't reused as if nothing had
    concurrency: int  calls in flight at once (default: hunch's, 16); a local model may want 1

Answers are cached, measured and compared like any engine's: `hunch test spec.yml --model ollama:qwen2.5:0.5b`,
`hunch diff spec.yml --model ollama:bespoke-minicheck` against the spec's own engine.
"""
from importlib.metadata import entry_points

_loaded: dict | None = None


def installed() -> dict:
    """{prefix: engine} for every installed plugin, loaded once. A plugin that fails to import is reported when a
    spec names it, not before: one broken package mustn't stop specs that don't use it."""
    global _loaded
    if _loaded is None:
        _loaded = {}
        for ep in entry_points(group="hunch.engines"):
            try:
                _loaded[ep.name] = ep.load()
            except Exception as e:  # noqa: BLE001 — kept, raised when a spec asks for it
                _loaded[ep.name] = e
    return _loaded


def get(model: str):
    """The plugin engine for `model` (its prefix), or None when no plugin has that prefix."""
    prefix = model.split(":", 1)[0] if ":" in model else None
    engine = installed().get(prefix) if prefix else None
    if isinstance(engine, Exception):
        raise SystemExit(f"engine {prefix!r} (plugin) failed to load: {engine!r}")
    return engine


def check(model: str, questions: dict, got: dict) -> dict:
    """The plugin's answers, or a SystemExit naming what is wrong with them: a missing answer or a wrong shape
    would otherwise surface far away, as a KeyError in `test`."""
    answers = got.get("answers") if isinstance(got, dict) else None
    if not isinstance(answers, dict):
        raise RuntimeError(f"{model}: answer() must return {{'answers': {{id: answer}}, …}}, got {type(got).__name__}")
    for rid, q in questions.items():
        a = answers.get(rid)
        need = {"noul": ("noul",), "choice": ("choice", "confidence", "probabilities"),
                "score": ("score", "confidence", "legend", "probabilities")}[q["type"]]
        if not isinstance(a, dict) or a.get("type") != q["type"] or any(k not in a for k in need):
            raise RuntimeError(f"{model}: answer for {rid!r} must be a {q['type']} with {list(need)}, got {a!r}")
        if q["type"] == "choice" and a["choice"] not in (q.get("criteria") or {}) and a["choice"] != "none_of_these":
            raise RuntimeError(f"{model}: {rid!r} answered {a['choice']!r}, not one of its options")
    return answers
