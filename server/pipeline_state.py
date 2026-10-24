"""Per-device pipeline state; persisted rows and public API payloads remain dictionaries.

Finalization owns done_to/progress/utts/voiced; the independent edge reader and
draft scheduler own draft_to/draft_utts/vad_to/wake. Neither cursor substitutes
for the other: a device can finalize a backlog while drafting current audio.
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import anyio

if TYPE_CHECKING:
    from segmenter import Segmenter


@dataclass
class PipelineState:
    seg: Segmenter | None = None
    done_to: dt.datetime | None = None
    audio_end: dt.datetime | None = None
    utts: list[tuple[dt.datetime, dt.datetime]] = field(default_factory=list)
    progress: list[tuple[float, dt.datetime]] = field(default_factory=list)
    final_latency: list[float] = field(default_factory=list)
    voiced: list[dict] = field(default_factory=list)
    refine: dict | None = None

    edge_end: dt.datetime | None = None
    last_speech: dt.datetime | None = None
    draft_to: dt.datetime | None = None
    draft_utts: list[tuple[dt.datetime, dt.datetime]] = field(default_factory=list)
    vad_to: dt.datetime | None = None
    wake: anyio.Event = field(default_factory=anyio.Event)
    drafts: list[dict] = field(default_factory=list)
    drafting: bool = False
    draft: dict | None = None
    latency: list[float] = field(default_factory=list)

    conversation: dict | None = None
    live: dict | None = None
    analyzing: bool = False
    ask: Callable[[], str | None] | None = None

    def finalized(self, hi: dt.datetime, now: float) -> None:
        """Advance the final cursor and its ten-minute processing-rate history."""
        self.done_to = hi
        self.progress = [p for p in self.progress if now - p[0] < 600] + [(now, hi)]

    def take_draft_wake(self) -> bool:
        """Consume this pause notification before a pass; new pauses wake the next pass."""
        by_vad = self.wake.is_set()
        self.wake = anyio.Event()
        return by_vad
