# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx", "pyyaml"]
# ///
"""The prototype's command, kept at this path so every command in the docs still works (`uv run hunch.py …`).
The code is the package in ../src/hunch; installed, the same command is `hunch …`."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from hunch.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
