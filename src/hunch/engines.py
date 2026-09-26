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
                      parsing changes, so old answers aren't reused as if nothing had. Without it the key holds the
                      plugin package's name and version, so an upgrade never serves the old version's answers
    concurrency: int  calls in flight at once (default: hunch's, 16); a local model may want 1

Answers are cached, measured and compared like any engine's: `hunch test spec.yml --model ollama:qwen2.5:0.5b`,
`hunch diff spec.yml --model ollama:bespoke-minicheck` against the spec's own engine.
"""
import inspect
from importlib.metadata import entry_points

RESERVED: set[str] = set()  # the built-in prefixes (core fills it in): a plugin can't take them over
_loaded: dict | None = None
_package: dict[str, str] = {}  # prefix → "name==version" of the package that registered it
shadowed: list[str] = []  # plugins that tried to take a built-in prefix, for lint to report


class ContractError(Exception):
    """A plugin broke the engine contract: retrying won't help, so the fill stops with this message."""


def installed() -> dict:
    """{prefix: engine} for every installed plugin, loaded once. A plugin that fails to import is reported when a
    spec names it, not before: one broken package mustn't stop specs that don't use it."""
    global _loaded
    if _loaded is None:
        _loaded = {}
        for ep in entry_points(group="hunch.engines"):
            if ep.name in RESERVED:  # its answers would be stored under the built-in's cache keys
                shadowed.append(f"{ep.name} ({ep.value})")
                continue
            if ep.dist is not None:
                _package[ep.name] = f"{ep.dist.name}=={ep.dist.version}"
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


def loaded() -> list[str]:
    return [p for p, e in installed().items() if not isinstance(e, Exception)]


async def call(engine, model: str, state, questions: dict) -> dict:
    got = engine.answer(model, state, questions)
    if not inspect.isawaitable(got):
        raise ContractError(f"{model}: the plugin's answer() must be async (async def answer(...))")
    return await got


def _p(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and 0.0 <= x <= 1.0


def adapter(model: str) -> str | None:
    """What goes in the cache key for a plugin engine's answers: its own `adapter`, else its package and version."""
    engine = get(model)
    if engine is None:
        return None
    return getattr(engine, "adapter", None) or _package.get(model.split(":", 1)[0]) or "unversioned"


def check(model: str, questions: dict, got) -> dict:
    """The plugin's answers, or a ContractError naming what is wrong with them: a missing answer, a wrong shape or
    a value that isn't a probability would otherwise surface far away, as a TypeError in `test`."""
    answers = got.get("answers") if isinstance(got, dict) else None
    if not isinstance(answers, dict):
        raise ContractError(f"{model}: answer() must return {{'answers': {{id: answer}}, …}}, got {type(got).__name__}")
    if not isinstance(got.get("cost", 0) or 0, (int, float)) or (got.get("cost") or 0) < 0:
        raise ContractError(f"{model}: 'cost' must be a number of USD ≥ 0, got {got.get('cost')!r}")
    for rid, q in questions.items():
        a = answers.get(rid)
        need = {"noul": ("noul",), "choice": ("choice", "confidence", "probabilities"),
                "score": ("score", "confidence", "legend", "probabilities")}[q["type"]]
        if not isinstance(a, dict) or a.get("type") != q["type"] or any(k not in a for k in need):
            raise ContractError(f"{model}: answer for {rid!r} must be a {q['type']} with {list(need)}, got {a!r}")
        bad = None
        if q["type"] == "noul":
            bad = None if _p(a["noul"]) else f"noul {a['noul']!r} is not a probability in [0, 1]"
        else:
            probs, allowed = a["probabilities"], (set(q.get("criteria") or {}) | {"none_of_these"} if q["type"] == "choice"
                                                  else {str(i) for i in range(len(q.get("criteria") or []))})
            if not _p(a["confidence"]):
                bad = f"confidence {a['confidence']!r} is not a probability in [0, 1]"
            elif not isinstance(probs, dict) or not all(_p(v) for v in probs.values()) or set(probs) - allowed:
                bad = f"probabilities must map its {'options' if q['type'] == 'choice' else 'levels 0…n-1'} to [0, 1], got {probs!r}"
            elif q["type"] == "choice" and a["choice"] not in allowed:
                bad = f"answered {a['choice']!r}, not one of its options"
            elif q["type"] == "score" and not (isinstance(a["score"], (int, float)) and 0 <= a["score"] <= len(allowed) - 1):
                bad = f"score {a['score']!r} is outside its levels 0…{len(allowed) - 1}"
        if bad:
            raise ContractError(f"{model}: {rid!r}: {bad}")
    return answers
