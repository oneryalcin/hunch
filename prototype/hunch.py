# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx", "pyyaml"]
# ///
"""The prototype's command, kept at this path so every command in the docs still works (`uv run hunch.py …`).
The code is the package in ../src/hunch; installed, the same command is `hunch …`."""
import importlib.util
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if __name__ == "__main__":
    sys.path.insert(0, str(SRC))
    from hunch.cli import main
    main()
else:  # `import hunch` from this folder found this file first: become the real package
    _spec = importlib.util.spec_from_file_location("hunch", SRC / "hunch" / "__init__.py",
                                                   submodule_search_locations=[str(SRC / "hunch")])
    _pkg = importlib.util.module_from_spec(_spec)
    sys.modules["hunch"] = _pkg
    _spec.loader.exec_module(_pkg)
