"""Group a time-ordered sentence stream into conversations: a reducer over four kinds of events.

    sentence(row)   a final sentence; opens a conversation or extends the open one; a gap of GAP_S closes it first
    advance(t)      the audio clock reached t without speech; closes the open conversation once the gap has passed
    end(t)          the recorder stopped at t; applied by the caller once the audio before t is processed
    tick(now)       wall clock; only for the IDLE_S safety net (the recorder died without saying so)

Every decision but tick uses the times carried by the events, so a network outage between the recorder
and the server does not split a conversation: the late sentences arrive with their original times.
The state is one open conversation; the caller stores `open` so a restart continues it.
"""
import datetime as dt
import time

# GAP_S: gpt-6-luna judged 180 pauses from 112 days of history: 2-5 min apart 62 % were still the same
# conversation, 5-10 min 45 %, 10-20 min 43 % (artifacts/record/history/gap_judge-*.jsonl)
GAP_S, MIN_S, MAX_S, IDLE_S = 300, 10, 4 * 3600, 20 * 60
IGNORE = {"远处", "far", "不确定", "未知", "unknown", "S?", "外放媒体"}  # S?: too short for a voiceprint; 外放媒体: old history labels
P = dt.datetime.fromisoformat


class Grouper:
    def __init__(self, gap_s=GAP_S, min_s=MIN_S, max_s=MAX_S, idle_s=IDLE_S, open_: dict | None = None):
        """open_: the conversation a restart continues."""
        self.gap, self.min, self.max, self.idle = gap_s, min_s, max_s, idle_s
        self.open = open_
        self.last_event = time.time()

    def sentence(self, s: dict) -> list[dict]:
        a, b = P(s["start"]), P(s["end"])
        out = self._close_if_stale(a)
        if self.open is None:  # an S? sentence opens one too: with ENGINE=local a conversation often starts with short ones
            self.open = dict(start=s["start"], end=s["end"], sentences=[], speech_s=0.0)
        self.open["sentences"].append(s)
        if s.get("speaker") not in IGNORE:  # an ignored speaker neither extends it nor counts as speech
            self.open["end"] = max(self.open["end"], s["end"])
            self.open["speech_s"] += (b - a).total_seconds()
        return self._touch(out)

    def advance(self, t: dt.datetime) -> list[dict]:
        return self._touch(self._close_if_stale(t))

    def end(self, t: dt.datetime) -> list[dict]:
        return self._touch(self._close() if self.open else [])

    def tick(self, now: float | None = None) -> list[dict]:
        if self.open and (now or time.time()) - self.last_event >= self.idle:
            return self._touch(self._close())
        return []

    def _close_if_stale(self, t: dt.datetime) -> list[dict]:
        c = self.open
        if c and ((t - P(c["end"])).total_seconds() >= self.gap or (t - P(c["start"])).total_seconds() >= self.max):
            return self._close()
        return []

    def _close(self) -> list[dict]:
        c, self.open = self.open, None
        if c["speech_s"] < self.min:
            return []
        c["participants"] = sorted({x.get("speaker") for x in c["sentences"] if x.get("speaker") and x["speaker"] not in IGNORE}, key=str)
        c["state"] = "closed"
        return [c]

    def _touch(self, out: list[dict]) -> list[dict]:
        self.last_event = time.time()
        return out


def split(sentences: list[dict], **kw) -> list[dict]:
    """Offline: fold a day's sentences through the same reducer."""
    g, out = Grouper(**kw), []
    for s in sorted(sentences, key=lambda s: s["start"]):
        out += g.sentence(s)
    if g.open:
        out += g.advance(P(g.open["end"]) + dt.timedelta(seconds=g.gap))
    return out


if __name__ == "__main__":
    T = lambda m, s=0: (dt.datetime(2026, 9, 29, 10) + dt.timedelta(minutes=m, seconds=s)).isoformat()
    rows = [dict(start=T(0), end=T(0, 20), speaker="我"), dict(start=T(1), end=T(1, 20), speaker="S1"),
            dict(start=T(2, 30), end=T(2, 40), speaker="远处"),      # far-field inside: kept, does not extend
            dict(start=T(6, 30), end=T(6, 35), speaker="我"),          # 5 min 10 s after the last real sentence: new one
            dict(start=T(6, 37), end=T(6, 39), speaker="我"),         # only 7 s of speech: dropped
            dict(start=T(20), end=T(20, 30), speaker="S2")]
    cs = split(rows)
    assert [len(c["sentences"]) for c in cs] == [3, 1], cs
    assert cs[0]["participants"] == ["S1", "我"] and cs[0]["end"] == T(1, 20)
    g = Grouper()  # an outage: no events for a while, then late sentences with their original times
    g.sentence(dict(start=T(0), end=T(0, 20), speaker="我"))
    g.last_event -= 10 * 60
    assert g.tick() == [] and g.open, "10 quiet minutes on the wall clock must not close it"
    assert g.sentence(dict(start=T(1), end=T(1, 30), speaker="S1")) == [] and len(g.open["sentences"]) == 2
    assert g.end(P(T(2))) and g.open is None, "end closes at once"
    g.sentence(dict(start=T(3), end=T(3, 20), speaker="我"))
    g.last_event -= IDLE_S
    assert g.tick() and g.open is None, "the idle safety net closes"
    print("ok", len(cs), "conversations")
