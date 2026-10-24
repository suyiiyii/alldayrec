"""diarist.py's self-check (synthetic voiceprints): online and close-time labels, rollback, names, engine groups, and
a named person who also passes the owner bank taking the closer label."""
import runpy
from pathlib import Path


def test_selfcheck():
    runpy.run_path(str(Path(__file__).with_name("alldayrec") / "diarist.py"), run_name="__main__")
