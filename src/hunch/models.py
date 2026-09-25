"""Pydantic classes as specs: the output type an agent returns is the judgment hunch tests.

Mapping (the same as Pydantic AI's TypeSafe model, so a class used as an agent's `output_type` becomes the spec
hunch tests, diffs and reviews):

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
