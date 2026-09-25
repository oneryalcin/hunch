"""Pydantic classes as specs: the output type an agent returns is the judgment hunch tests.

Mapping (types as in Pydantic AI's decision models). The wording is not the same: Pydantic AI >= 2.50 sends each
question as structured parts (field name, the class docstring as goal, the description as question, the agent's
instructions as framing, BoolCriteria), while this sends the description alone, so the same class can get different
answers here and in an agent. For the agent's exact wording, use `spec_from_agent` (below).

    bool                      → noul       yes/no
    Literal[...] / Enum       → choice     option descriptions: Field(json_schema_extra={"options": {...}}), or
                                           Member.__doc__ = "..." set after the class (a string under a member
                                           in the class body is not attached to it)
    X | None (Optional)       → choice with none_of_these
    list[Literal] / list[Enum]→ multi      one yes/no per option
    IntEnum                   → score      levels in value order; describe them with "options" by member
                                           name, or Member.__doc__, else the member name

A field's description is the question's instructions. hunch-only settings (act, gold, escalate, none) go in
Field(json_schema_extra={"hunch": {...}}); they never reach the engine.
"""
import concurrent.futures
import enum
import re
import types
from typing import Literal, Union, get_args, get_origin

NONE_TEXT = "None of the options fits"


def _member_doc(m: enum.Enum) -> str:
    doc = m.__dict__.get("__doc__") or ""  # only a docstring set on the member itself, not the class's
    return doc.strip()


def _options(t) -> tuple[list[str], dict[str, str]]:
    if get_origin(t) is Literal:
        return [str(a) for a in get_args(t)], {}
    if isinstance(t, type) and issubclass(t, enum.Enum):
        labels = [str(m.value) if isinstance(m.value, str) else m.name for m in t]
        return labels, {lab: _member_doc(m) for lab, m in zip(labels, t)}
    raise TypeError(f"{t!r}: expected bool, Literal, Enum, IntEnum, list of those, or Optional of those")


def _unwrap_optional(t) -> tuple[object, bool]:
    if get_origin(t) in (Union, types.UnionType) and type(None) in get_args(t):
        rest = [a for a in get_args(t) if a is not type(None)]
        if len(rest) == 1:
            return rest[0], True
    return t, False


def question_of_field(name: str, field) -> dict:
    t, optional = _unwrap_optional(field.annotation)
    extra = field.json_schema_extra if isinstance(field.json_schema_extra, dict) else {}
    descs, hunch_opts = extra.get("options") or {}, dict(extra.get("hunch") or {})
    instructions = field.description or name.replace("_", " ").capitalize() + "?"
    if t is bool:
        q = {"type": "noul", "instructions": instructions}
    elif get_origin(t) is list:
        labels, docs = _options(get_args(t)[0])
        q = {"type": "multi", "instructions": instructions, "criteria": {lab: descs.get(lab, docs.get(lab, "")) for lab in labels}}
    elif isinstance(t, type) and issubclass(t, enum.IntEnum):
        members = sorted(t, key=lambda m: m.value)
        if [m.value for m in members] != list(range(len(members))):
            raise TypeError(f"{name}: an IntEnum rubric needs levels 0..n-1, got {[m.value for m in members]}")
        q = {"type": "score", "instructions": instructions,
             "criteria": [descs.get(m.name) or _member_doc(m) or m.name.replace("_", " ").lower() for m in members]}
    else:
        labels, docs = _options(t)
        q = {"type": "choice", "instructions": instructions, "criteria": {lab: descs.get(lab, docs.get(lab, "")) for lab in labels}}
        if optional:
            q["none"] = hunch_opts.pop("none", NONE_TEXT)
    return q | hunch_opts


def as_model(output_type, description: str | None = None):
    """A Pydantic AI `output_type` that is a bare type (Literal[...], bool, an Enum, list[...]) as a one-field
    model, `output`: what the agent returns is then one question."""
    from pydantic import BaseModel, Field, create_model
    if isinstance(output_type, type) and issubclass(output_type, BaseModel):
        return output_type
    return create_model("Output", output=(output_type, Field(description=description)))


def spec_from_model(cls, *, judgment: str | None = None, model: str = "jev-1.13.0", source: str | None = None,
                    key: str = "id", state: list[str] | None = None, description: str | None = None, **spec) -> dict:
    """A hunch spec (the dict form of the YAML) whose questions are the class's fields. `cls` may also be a bare
    output type (Literal[...], bool, ...), which becomes one question, `output`, asked with `description`."""
    cls = as_model(cls, description)
    name = judgment or re.sub(r"(?<!^)(?=[A-Z])", "_", cls.__name__).lower()
    out = {"judgment": name, "model": model, "key": key, "state": list(state or []),
           "questions": {n: question_of_field(n, f) for n, f in cls.model_fields.items()}}
    if source is not None:
        out["source"] = source
    return out | spec


def spec_from_agent(agent, *, state: str, judgment: str | None = None, model: str | None = None,
                    source: str | None = None, key: str = "id", deps=None, **spec) -> dict:
    """A hunch spec whose questions are the ones a Pydantic AI agent (>= 2.50, on a decision model) sends, recorded
    from Pydantic AI itself: its first request is captured and the run stopped, so no model is called. Word for
    word the same questions, and `state` (the column holding the agent's prompt) is sent bare, as the agent sends
    it; on the same engine, hunch and the agent then make the same request for a row.

    Question names are Pydantic AI's with "." as "__" (a list field's `topics.refund` is `topics__refund`).
    hunch-only settings still go in Field(json_schema_extra={"hunch": {...}}), applied to the field's questions.
    Refused, since hunch could not send what the agent sends: agents with several routes (tools, or a union of
    output types: a route question comes first), a system_prompt (the state becomes a conversation), and
    instructions that depend on the prompt (they would differ per row). Instructions may depend on `deps`."""
    import dataclasses

    if not isinstance(state, str):
        raise TypeError("state: the one column holding the agent's prompt (a list would be sent as a dict, not as the agent sends it)")
    first, second = (_record(agent, p, deps) for p in ("-", "a different prompt"))
    seen = first[1]
    if not isinstance(first[0], str):
        raise ValueError("the agent sends more than its prompt (a system_prompt or history), which a spec's state can't express; "
                         "move a system_prompt into instructions")
    if {k: dataclasses.asdict(q) for k, q in seen.items()} != {k: dataclasses.asdict(q) for k, q in second[1].items()}:
        raise ValueError("the agent's questions depend on the prompt (dynamic instructions), so each row would be asked "
                         "differently; make them depend on deps, not on the prompt")
    if "route" in seen:
        raise ValueError(f"the agent picks between routes ({', '.join(q for q in seen['route'].criteria)}) before "
                         f"it asks anything else; spec_from_agent reads agents with one output type and no tools")
    questions = {name.replace(".", "__"): _plain(dataclasses.asdict(q)) for name, q in seen.items()}
    for field, opts in _hunch_options(getattr(agent, "output_type", None)).items():
        for qid in questions:
            if qid == field or qid.startswith(field + "__"):
                questions[qid] |= opts
    name = getattr(getattr(agent, "output_type", None), "__name__", "agent")
    out = {"judgment": judgment or re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower(), "model": model or _engine_of(agent.model), "key": key, "state": state,
           "questions": questions}
    if source is not None:
        out["source"] = source
    return out | spec


def _record(agent, prompt: str, deps) -> tuple[object, dict]:
    """(state, questions) of the agent's first decision request for this prompt, with no model call. On a thread
    of its own, so it works from async code too (run_sync can't run inside a running loop)."""
    from pydantic_ai.models.decision import DecisionModel

    class Captured(Exception):
        pass

    seen = {}

    class Recorder(DecisionModel[None]):
        model_name = property(lambda self: "hunch-recorder")
        system = property(lambda self: "hunch")
        base_url = property(lambda self: "hunch://recorder")

        async def decide(self, request, model_settings):
            seen.update(state=request.state, questions=dict(request.questions))
            raise Captured

    def run():
        try:
            with agent.override(model=Recorder()):
                agent.run_sync(prompt, deps=deps)
        except Captured:
            pass

    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        pool.submit(run).result()
    if not seen:
        raise ValueError("the agent sent no decision request (does its output type need a language model?)")
    return seen["state"], seen["questions"]


def _plain(d: dict) -> dict:
    """A recorded question as spec YAML: keys Pydantic AI left empty are not sent, so they are not written."""
    q = {k: v for k, v in d.items() if v is not None}
    if q.get("type") == "noul" and isinstance(q.get("criteria"), dict):  # a choice keeps its undescribed options
        q["criteria"] = {k: v for k, v in q["criteria"].items() if v is not None} or None
    order = ("type", "instructions", "criteria")  # as a person reads a question; key order is not sent meaning
    return {k: q[k] for k in (*order, *q) if k in q and q[k] is not None}


def _hunch_options(output_type) -> dict[str, dict]:
    fields = getattr(output_type, "model_fields", None) or {}
    extras = {n: f.json_schema_extra for n, f in fields.items() if isinstance(f.json_schema_extra, dict)}
    return {n: dict(x["hunch"]) for n, x in extras.items() if x.get("hunch")}


def _engine_of(m) -> str:
    """hunch's name for the agent's model: TypeSafe's Jev by its version. Anything else: pass model=."""
    name = m if isinstance(m, str) else (getattr(m, "model_name", None) if getattr(m, "system", None) == "typesafe" else None)
    if isinstance(name, str) and name.startswith("typesafe:"):
        name = name.split(":", 1)[1]
    if not name or ":" in name:
        raise ValueError(f"which engine should hunch ask? The agent's model is {m if isinstance(m, str) else type(m).__name__}; "
                         f"pass model= (e.g. jev-1.13.0)")
    return name


def to_model(cls, answers: dict[str, dict]):
    if not hasattr(cls, "model_fields"):  # a bare output type: return the value itself
        return to_model(as_model(cls), answers).output
    return _to_model(cls, answers)


def _to_model(cls, answers: dict[str, dict]):
    """An instance of the class from hunch's answers for one row ({question: {"label": ...}}), with the
    types the fields declare: bool, the Literal/Enum value, a list for multi, the IntEnum level, None for none."""
    values = {}
    for name, field in cls.model_fields.items():
        t, _ = _unwrap_optional(field.annotation)
        if get_origin(t) is list:
            item_t = get_args(t)[0]
            chosen = [lab for lab in _options(item_t)[0]
                      if (answers.get(f"{name}__{lab}") or {}).get("label") == "yes"]
            values[name] = [_value(item_t, lab) for lab in chosen]
            continue
        a = answers.get(name)
        if a is None:
            continue
        label = a["label"]
        if t is bool:
            values[name] = label == "yes"
        elif isinstance(t, type) and issubclass(t, enum.IntEnum):
            values[name] = t(int(label.split(":", 1)[0]))
        elif label == "none_of_these":
            values[name] = None
        else:
            values[name] = _value(t, label)
    return cls(**values)


def _value(t, label: str):
    if isinstance(t, type) and issubclass(t, enum.Enum):
        return next(m for m in t if (str(m.value) if isinstance(m.value, str) else m.name) == label)
    if get_origin(t) is Literal:  # Literal[1, 2] was asked as "1", "2": return the declared value
        return next((a for a in get_args(t) if str(a) == label), label)
    return label
