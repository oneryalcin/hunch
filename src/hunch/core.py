"""What is left of hunch.core, which held all of hunch until 0.4: its code now lives in spec, store, answers, lint,
fill, execute, measure, online, suggest, diff, review and commands, and the run settings in hunch.settings.

It still gives hunch-engine-ollama 0.1 its two names (remove when that plugin needs hunch-ai>=0.4). Reading any other
old name says where it went; setting a run setting here is an error, since it would set a name nothing reads (before
0.4, `hunch.core.MAX_COST = 0.0` was the documented cap).
"""
import importlib
import sys

from hunch.fill import llm_answer, llm_prompt  # noqa: F401  (hunch-engine-ollama 0.1)

MOVED = {"SAMPLE", "TARGET", "STORE", "VERBOSE", "MAX_COST", "CHARGED", "SPEND_LIMIT", "RETRIES"}
MODULES = ("spec", "store", "answers", "lint", "fill", "execute", "measure", "online", "suggest", "diff", "review",
           "commands")


class _Core(type(sys)):
    def __getattr__(self, name):
        if name in MOVED:
            raise AttributeError(f"hunch.core.{name} moved to hunch.settings.{name}")
        home = next((m for m in MODULES if hasattr(importlib.import_module(f"hunch.{m}"), name)), None)
        raise AttributeError(f"hunch.core.{name} moved to hunch.{home}.{name}" if home
                             else f"module 'hunch.core' has no attribute {name!r}")

    def __setattr__(self, name, value):
        if name in MOVED:
            raise AttributeError(f"hunch.core.{name} moved: set hunch.settings.{name}")
        super().__setattr__(name, value)


sys.modules[__name__].__class__ = _Core
