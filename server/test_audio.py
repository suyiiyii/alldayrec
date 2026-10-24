"""audio.py's self-check (synthetic tones): the stream's order, resume point, late pieces, and holds."""
import runpy
from pathlib import Path


def test_selfcheck():
    runpy.run_path(str(Path(__file__).with_name("alldayrec") / "audio.py"), run_name="__main__")
