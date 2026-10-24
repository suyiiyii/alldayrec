"""summary.py's self-check: a model reply's times resolved to rows (prompt v9: each person's point too), due dates, media."""
import runpy
from pathlib import Path


def test_selfcheck():
    runpy.run_path(str(Path(__file__).with_name("alldayrec") / "summary.py"), run_name="__main__")
