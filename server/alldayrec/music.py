"""Music in a sentence's audio (a song played in the room, singing over its accompaniment), so that it never becomes a
voice: YAMNet (AudioSet's 521 classes, infer.YAMNET) over the sentence brought to -20 dBFS; its score is the mean over
YAMNet's 0.96 s frames of P(Music) - P(Speech), and MUSIC_MIN or more is music. The level matters: a song from a
speaker across the room reaches the recorder at about -61 dBFS (the people talking near it: -43), and as it came
YAMNet heard silence in it (2 % of the sung sentences scored 0.3).

Measured 10-24 on the test instance, on sentences as the final layer cuts them (at most 15 s): 0 of 1752 sentences of
10 conversations without media scored 0.2 (the highest 0.03); 72 % of 492 sentences in the songs of 6 conversations
did (the rest mostly faint lyrics, -60 to -74 dBFS, half of them under 1.5 s), 1.2 % of the owner's 329 sentences said
while those songs played, 5 % of 276 sentences of videos. Synthetic (3 TTS voices, 6 instrumentals, 2 songs): songs
and instrumentals alone 100 %, speech alone 0 %, speech over music 43 % with the speech 6 dB louder, 80 % at the same
level, 88 % with the music 3 dB louder. CPU: ~8 ms per second of audio on one core of a 16-core desktop, 3 % of what
the sentence's voiceprint takes.

MUSIC_DETECT=0 turns it off: every sentence gets a voiceprint, as before.
"""
import os
from functools import cache

import numpy as np

from alldayrec import infer

ON = os.environ.get("MUSIC_DETECT", "1") != "0"
MUSIC_MIN = 0.2
SPEECH, MUSIC = 0, 132  # YAMNet's class indices (yamnet_class_map.csv)


@cache
def _yamnet():
    return infer.session(infer.YAMNET, 1)


def score(x: np.ndarray) -> float:
    """int16 16 kHz samples -> mean P(Music) - P(Speech) over YAMNet's frames, -1 to 1."""
    rms = np.sqrt(np.mean(np.square(x, dtype=np.float64))) / 32768
    p = _yamnet().run(None, {"wav": (x * (0.1 / max(rms, 1e-4) / 32768)).astype(np.float32)})[0]  # -20 dBFS, at most +60 dB
    return float(np.mean(p[:, MUSIC] - p[:, SPEECH]))


def heard(x: np.ndarray) -> bool:
    return score(x) >= MUSIC_MIN
