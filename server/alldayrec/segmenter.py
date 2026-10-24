"""Where the speech is and where the final pieces are cut: FireRed Stream-VAD over a device's audio stream (Segmenter),
the loose VAD's speech in a stretch of it, and the final layer's cut points."""
import copy
import datetime as dt
import os

import numpy as np

from alldayrec import infer


SR = 16000
FRAME_LEN, FRAME_SHIFT = 400, 160          # FireRed fbank: 25 ms window, 10 ms shift
MERGE_GAP_S = 0.6                          # VAD utterances closer than this are one
MAX_UTTERANCE_S = 15.0
CHUNK_S = 300.0                            # final layer: most audio in one piece (one engine call; 15 min in one call timed out)
QUIET_S = 30.0                             # a pause this long ends a final piece early, 15 s into it
CUT_SEARCH_S = 60.0                        # a full piece ends in the widest pause of its last CUT_SEARCH_S
SQUEEZE_VAD = float(os.environ.get("SQUEEZE_VAD", "0.3"))  # ... silences by this VAD threshold: 0.5 (cutting pieces) misses short replies
BUFFER_S = 900                             # audio kept for refinement; pieces are fed only while finalization is within BUFFER_S - 300


class Segmenter:
    """FireRed Stream-VAD over a continuous sample stream; emits (start, end, samples) utterances."""

    def __init__(self):
        self.vad = infer.StreamVad()
        self.loose = copy.deepcopy(self.vad.postprocessor)  # the same frames at SQUEEZE_VAD: only what the engine is sent
        self.loose.speech_threshold = SQUEEZE_VAD
        self.speech: list[tuple[int, int]] = []  # its speech in the buffer (stream samples)
        self.buf = np.zeros(0, np.int16)      # samples not yet turned into frames
        self.audio = np.zeros(0, np.int16)    # recent samples for cutting utterances
        self.audio_base = 0                   # stream sample index of audio[0]
        self.anchors: list[tuple[int, dt.datetime]] = []  # (stream sample index, wall time)
        self.samples = 0
        self.frames = 0
        self.pending = None                   # utterance waiting for a possible merge

    def time_at(self, sample: int) -> dt.datetime:
        idx, t = next((a for a in reversed(self.anchors) if a[0] <= sample), self.anchors[0])
        return t + dt.timedelta(seconds=(sample - idx) / SR)

    def push(self, t: dt.datetime, x: np.ndarray) -> list:
        if not self.anchors or abs((self.time_at(self.samples) - t).total_seconds()) > 0.5:
            self.anchors.append((self.samples, t))  # new file or a gap: re-anchor the clock
        self.samples += len(x)
        self.audio = np.concatenate([self.audio, x])
        self.buf = np.concatenate([self.buf, x])
        out = []
        if len(self.buf) >= FRAME_LEN:
            k = (len(self.buf) - FRAME_LEN) // FRAME_SHIFT + 1
            # Only whole frames, keeping the overlap for the next call: splitting at arbitrary
            # sample counts drops frames at every boundary and the clock drifts ~2 %.
            for r in self.vad.detect_chunk(self.buf[:(k - 1) * FRAME_SHIFT + FRAME_LEN]):
                q = self.loose.process_one_frame(r.raw_prob)
                if q.is_speech_end:
                    self.speech.append(((q.speech_start_frame - 1) * FRAME_SHIFT, (q.speech_end_frame - 1) * FRAME_SHIFT))
                if r.is_speech_end:
                    out += self._utterance(r.speech_start_frame - 1, r.speech_end_frame - 1)
            self.frames += k
            self.buf = self.buf[k * FRAME_SHIFT:]
        out += self._flush_if_quiet()
        keep = self.samples - BUFFER_S * SR
        if keep > self.audio_base:
            self.audio = self.audio[keep - self.audio_base:]
            self.audio_base = keep
            self.speech = [u for u in self.speech if u[1] > keep]
        return out

    def speaking(self) -> bool:
        """An utterance is still open (the VAD is in speech, or one waits for a merge): last_speech does not cover it yet."""
        return self.pending is not None or self.vad.postprocessor.state.name != "SILENCE"

    def open_speech(self) -> int | None:
        """Stream sample where the loose VAD's speech still running started (None in silence)."""
        p = self.loose
        if p.state.name == "SILENCE":
            return None
        f = p.frame_cnt - p.speech_cnt + 1 - p.pad_start_frame if p.state.name == "POSSIBLE_SPEECH" else p.last_speech_start_frame
        return max(0, f - 1) * FRAME_SHIFT

    def sample_at(self, t: dt.datetime) -> int:
        i = next((k for k in range(len(self.anchors) - 1, -1, -1) if self.anchors[k][1] <= t), 0)
        idx, t0 = self.anchors[i]
        s = idx + round((t - t0).total_seconds() * SR)  # time_at's times are to the µs: the sample they came from
        # a time in the gap before the next session has no audio: the first sample after the gap
        return min(s, self.anchors[i + 1][0]) if i + 1 < len(self.anchors) else s

    def window(self, seconds: float, start: dt.datetime | None = None) -> tuple[dt.datetime, np.ndarray]:
        """Up to `seconds` of audio from `start` (default: the last `seconds` up to now)."""
        if start is None:
            a = max(self.audio_base, self.samples - int(seconds * SR))
        else:
            a = min(max(self.audio_base, self.sample_at(start)), self.samples)
        b = min(self.samples, a + int(seconds * SR))
        return self.time_at(a), self.audio[a - self.audio_base:b - self.audio_base]

    def _utterance(self, f0: int, f1: int) -> list:
        a, b = f0 * FRAME_SHIFT, f1 * FRAME_SHIFT
        p = self.pending
        if p and (a - p[1]) / SR < MERGE_GAP_S and (b - p[0]) / SR <= MAX_UTTERANCE_S:
            self.pending = (p[0], b)
            return []
        out = self._emit() if p else []
        self.pending = (a, b)
        return out

    def _flush_if_quiet(self) -> list:
        if self.pending and (self.frames * FRAME_SHIFT - self.pending[1]) / SR >= MERGE_GAP_S \
                and self.vad.postprocessor.state.name == "SILENCE":
            return self._emit()
        return []

    def _emit(self) -> list:
        a, b = self.pending
        self.pending = None
        x = self.audio[max(0, a - self.audio_base):max(0, b - self.audio_base)]
        return [(self.time_at(a), self.time_at(b), x)] if len(x) else []


def next_cut(utts: list[tuple[float, float]], end: float, speaking: bool, force: bool = False) -> float | None:
    """Where the next final piece ends, in seconds after the finalized time, or None to wait for more audio.

    utts: VAD utterances (start, end) after the finalized time, in time order; end: the audio fed so far; speaking:
    speech runs on at `end` (its start may be unknown). The piece ends QUIET_S / 2 into the first pause of QUIET_S
    (leading and trailing silence count), else, once CHUNK_S of audio is there, in the middle of the widest pause of its
    last CUT_SEARCH_S (CHUNK_S itself when there is none). force (the recorder stopped, all audio in): at `end`, even
    in speech: no more audio comes, and the VAD may never see the end of an utterance the recording cut off.
    Otherwise every cut lies in known silence or at CHUNK_S, so the engine always has the audio after a sentence.
    """
    bounds = [(0.0, 0.0)] + utts
    gaps = [(b1, a2) for (_, b1), (a2, _) in zip(bounds, bounds[1:])]
    if not speaking:
        gaps.append((bounds[-1][1], end))
    for lo, hi in gaps:
        if lo + QUIET_S / 2 > CHUNK_S:
            break
        if hi - lo >= QUIET_S:
            return lo + QUIET_S / 2
    if end >= CHUNK_S:
        near = [(hi - lo, (lo + hi) / 2) for lo, hi in gaps if CHUNK_S - CUT_SEARCH_S <= (lo + hi) / 2 <= CHUNK_S and hi > lo]
        return max(near)[1] if near else CHUNK_S
    if force and end > 0.05:
        return end
    return None


def speech_in(seg: Segmenter, a0: int, n: int) -> list[tuple[float, float]]:
    """The loose VAD's speech (SQUEEZE_VAD) in the n samples from stream sample a0, in seconds into them, the speech
    still running too."""
    o = seg.open_speech()
    return [((a - a0) / SR, (b - a0) / SR) for a, b in seg.speech + ([(o, a0 + n)] if o is not None else [])]
