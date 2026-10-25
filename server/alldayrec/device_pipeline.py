"""A device's pipeline: its audio stream through the final layer (pieces to the engine once, sentences, conversations)
and, while a page watches and the device records, the draft layer and the live analysis."""
import datetime as dt
import json
import logging
import math
import os
import re
import time

import anyio
import anyio.to_thread
import numpy as np

from alldayrec import asr
from alldayrec import audio
from alldayrec import db
from alldayrec import live
from alldayrec import perf
from alldayrec import qwen_stream
from alldayrec import summary
from alldayrec import threads
from alldayrec import ws
from alldayrec.conversations import IGNORE, Grouper
from alldayrec.core import iso, owner_npz, state
from alldayrec.pipeline_state import PipelineState
from alldayrec.segmenter import BUFFER_S, MERGE_GAP_S, SR, Segmenter, next_cut, speech_in
from alldayrec.tenant import T
from alldayrec.transcript import EDGE_S, clip_turns, final_rows, label_sentences, overlaps

log = logging.getLogger("live")


VAD_CUT_S = 0.5                            # a VAD-driven pass ends this far into the pause (< MERGE_GAP_S: still silence)
REFINE_EVERY_S = int(os.environ.get("REFINE_EVERY_S", "30"))  # draft layer: a pass after this long without a pause
REFINE_WINDOW_S = int(os.environ.get("REFINE_WINDOW_S", "240"))    # draft layer: most audio one pass takes
REFINE_MARGIN_S = 1.0                      # audio re-read before the last drafted time: the engine's sentence starts come a little late
DRAFT_BACK_S = 300                         # a page that opens gets drafts this far back at most (the minutes not final yet)
RELABEL_GROUPS = True                      # relabel at a close: the engine's speaker ids in a piece as units, split when their voices disagree
LIVE_AUDIO_S = 120                         # a device whose audio came this recently is recording now: drafts and live analysis
LIVE_EVERY_S = int(os.environ.get("LIVE_EVERY_S", "120"))  # new speech in the open conversation before the live analysis is redone


async def pipeline(device: str):
    """Two layers over one audio stream. The final layer always runs: pieces of up to CHUNK_S cut in pauses go whole
    to the engine once, become the device's sentences, conversations and summaries. The draft layer runs only while
    a page watches and the device is recording now, on its own stream of the device's newest audio (edge_feed): each
    pause its VAD sees sends the stretch before it at once; its rows go to the drafts table and the page until the
    final layer covers their time."""
    seg, dseg = Segmenter(), Segmenter()  # the final layer's audio, the draft layer's
    resume = db.kv(f"finalized_to:{device}")
    done0 = dt.datetime.fromisoformat(resume) if resume else None
    draft0 = db.kv(f"draft_to:{device}")
    st = T().devices[device] = PipelineState(
        seg=seg, done_to=done0,
        draft_to=dt.datetime.fromisoformat(draft0) if draft0 else None,
        drafts=db.drafts(iso(done0) if done0 else "", None, device))
    if resume:
        log.info("%s resuming after the last finalized row at %s", device, resume)
    fcost, dcost = {}, {}  # what the final and the draft layer spent since their last perf_pieces row (perf.piece)

    async def feed():
        """The device's audio stream into the final layer's segmenter, taking the next stretch only while finalization
        is less than BUFFER_S - 300 s of audio behind: the buffer must not drop audio before it is finalized."""
        async for t, x in audio.stream(device, st.done_to, spent=fcost):
            while True:
                done = st.done_to
                a = max(seg.audio_base, seg.sample_at(done)) if seg.anchors and done else seg.audio_base
                if (seg.samples - a) / SR <= BUFFER_S - 300:
                    break
                await anyio.sleep(1)
            st.audio_end = t + dt.timedelta(seconds=len(x) / SR)
            with perf.timed(fcost, "vad_ms"):
                try:
                    st.utts += [(u[0], u[1]) for u in await seg.apush(t, x)]
                except Exception:
                    log.exception("%s: segmenter failed on %.1f s at %s", device, len(x) / SR, t.time())

    async def edge_feed():
        """The draft layer's own stream: the device's newest DRAFT_BACK_S of audio, whatever the final layer is busy
        with. A backlog (a phone back after hours offline sends them at once) is skipped here and finalized in order
        by the final layer; fed through feed(), drafts waited until it was all final (10-06: hours)."""
        async for t, x in audio.stream(device, None, edge_s=DRAFT_BACK_S, spent=dcost):
            st.edge_end = t + dt.timedelta(seconds=len(x) / SR)
            with perf.timed(dcost, "vad_ms"):
                try:
                    utts = await dseg.apush(t, x)
                except Exception:
                    log.exception("%s: draft segmenter failed on %.1f s at %s", device, len(x) / SR, t.time())
                    continue
            for i, u in enumerate(utts):
                st.last_speech = u[1]  # a piece with no VAD speech is not sent to the engine
                st.draft_utts.append((u[0], u[1]))
                nxt = utts[i + 1][0] if i + 1 < len(utts) else dseg.time_at(dseg.pending[0]) if dseg.pending else None
                if nxt is None or (nxt - u[1]).total_seconds() >= MERGE_GAP_S:  # a pause, not a split of a long utterance
                    st.vad_to = u[1] + dt.timedelta(seconds=VAD_CUT_S)
                    st.wake.set()
            del st.draft_utts[:-200]

    from alldayrec.diarist import Diarist, relabel
    bank = {k: e for k, (_, e) in db.people().items()}
    vp = owner_npz()
    owner = dict(np.load(vp)) if vp.exists() else {}  # a new instance has nobody's voiceprint yet: nobody is 我 (selfvoice)
    embed, dia, grouper = state["embed"], Diarist(owner, people=bank), Grouper(open_=db.kv(f"open_conversation:{device}"))
    ddia = Diarist(owner, people=bank)  # the drafts' own clustering: it never touches the final labels
    T().dias.extend([dia, ddia])  # a rename on the conversation page teaches every diarist of the tenant a name
    live_failed = 0.0

    def open_view() -> dict | None:
        """The conversation going on now as the pages and the live analysis see it: the open conversation's final rows
        and the drafts after the finalized time; drafts alone start one before any final row has come."""
        o, done = grouper.open, st.done_to
        ds = [r for r in st.drafts if not done or r["start"] >= iso(done)]
        if o and ds and (dt.datetime.fromisoformat(ds[0]["start"]) - dt.datetime.fromisoformat(o["end"])).total_seconds() >= grouper.gap:
            o = None  # the drafts begin after the open conversation's gap: a new one
        if not o and not ds:
            return None
        speech = sum((dt.datetime.fromisoformat(r["end"]) - dt.datetime.fromisoformat(r["start"])).total_seconds()
                     for r in ds if r.get("speaker") not in IGNORE)
        rows = (o["sentences"] if o else []) + ds
        return {"start": o["start"] if o else ds[0]["start"], "end": max(r["end"] for r in rows),
                "sentences": rows, "speech_s": (o["speech_s"] if o else 0.0) + speech}

    def show_open():
        v = open_view()
        st.conversation = {"start": v["start"], "end": v["end"], "sentences": len(v["sentences"]), "speech_s": round(v["speech_s"]),
                              "device": device} if v else None

    if (lv := db.kv(f"live:{device}")) and "messages" in lv and (v := open_view()) and overlaps(lv, v):
        st.live = lv
    show_open()
    if grouper.open:
        log.info("%s continuing the open conversation from %s", device, grouper.open["start"])
        ws.note(f"{device}：接着上次打开的对话 {grouper.open['start'][11:19]}", "state")
    else:
        ws.note(f"{device}：开始处理", "state")

    def record(c: dict) -> dict:
        cid = "c" + c["start"].replace("-", "").replace(":", "").replace("T", "")[:14]
        if (other := db.conversation(cid)) and other.get("device") != device:  # two devices starting the same second
            cid += "-" + re.sub(r"[^\w]", "_", device)
        return {"id": cid, "start": c["start"], "end": c["end"], "participants": c["participants"],
                "sentences": len(c["sentences"]), "speech_s": round(c["speech_s"], 1), "device": device}

    def relabel_rows(rec: dict) -> tuple[list[dict], int] | None:
        """A closed conversation's final rows labelled again with all its sentences in view (diarist.relabel), and how
        many changed; None when its voices do not cover its rows (a restart lost the earlier ones)."""
        vs = [v for v in st.voiced if rec["start"] <= v["start"] <= rec["end"]]
        rows = db.sentences(rec["start"], rec["end"], inclusive=True, source=device)
        if not vs or not rows:
            return None
        labels = relabel([v["e"] for v in vs], [v["dur"] for v in vs], owner,
                         [v["key"] or ("alone", i) for i, v in enumerate(vs)] if RELABEL_GROUPS else None, people=dia.people)
        changed, missing = 0, 0
        for r in rows:
            mine = [(v["dur"], lab) for v, lab in zip(vs, labels) if r["start"] <= v["start"] <= r["end"]]
            if not mine:
                missing += 1
                continue
            new = max(mine)[1]  # a row of several sentences: its longest one's
            if new != r.get("speaker"):
                r["speaker"], changed = new, changed + 1
        return (rows, changed) if missing <= len(rows) // 10 else None

    async def commit(closed: list[dict], rows: list[dict] | None = None, lo: str = "", hi: dt.datetime | None = None):
        """One transaction: the piece's rows replace [lo, hi), closed conversations (with the live analysis that
        overlaps one), the finalized time, the open conversation."""
        recs = [record(c) for c in closed]
        lv = st.live
        with db.tx() as t:
            if rows is not None:
                db.replace_sentences(t, lo, iso(hi), rows, device)
            attached, relabelled = False, []
            for rec in recs:
                if (rl := relabel_rows(rec)) and rl[1]:  # every label again, with the whole conversation in view
                    t.executemany("UPDATE sentences SET speaker = ? WHERE source = ? AND id = ?", [(r["speaker"], device, r["id"]) for r in rl[0]])
                    rec["participants"] = sorted({r["speaker"] for r in rl[0] if r.get("speaker") and r["speaker"] not in IGNORE}, key=str)
                    relabelled.append((rec, rl[0], rl[1]))
                    if lv and overlaps(lv, rec):
                        lv = {**lv, "drafted": True}  # its agent saw other labels: the closing turn resends the final transcript
                st.voiced = [v for v in st.voiced if v["start"] > rec["end"]]
                db.insert_conversation(t, rec)
                db.put_summary({"conversation": rec["id"], "status": "pending", "error": None}, t)
                if lv and overlaps(lv, rec):
                    db.put_timeline(rec["id"], lv, t)
                    rec["via_live"] = True  # its agent writes the final summary
                    attached = True
            if hi:
                # to the µs, the sample it is (Segmenter.sample_at): to the ms, a restart took the last samples
                # before it as not final yet (10-24: 11 samples alone to the engine, again and again)
                db.put_kv(f"finalized_to:{device}", hi.isoformat(timespec="microseconds"), t)
            db.put_kv(f"open_conversation:{device}", grouper.open, t)
            if hi:
                st.drafts = [r for r in st.drafts if r["start"] >= iso(hi)]  # covered by final rows now
            # the analysis ends with its conversation, or when nothing is going on any more (a too short one is dropped)
            live_ends = bool(lv) and (attached or open_view() is None)
            if live_ends:
                db.put_kv(f"live:{device}", None, t)
        if hi:
            st.finalized(hi, time.time())
        if rows is not None:
            await ws.broadcast({"type": "final", "device": device, "from": lo, "to": iso(hi), "rows": rows})
        for rec, rs, n in relabelled:
            log.info("%s relabelled %d of %d rows of %s", device, n, len(rs), rec["id"])
            ws.note(f"对话 {rec['start'][11:19]}：整段重新认人，{len(rs)} 句里改了 {n} 句", "state")
            await ws.broadcast({"type": "final", "device": device, "from": rec["start"],
                             "to": iso(dt.datetime.fromisoformat(rec["end"]) + dt.timedelta(milliseconds=1)), "rows": rs})
        for rec in recs:
            dia.reset()
            ddia.reset()
            T().summaries[0].send_nowait(rec)
            log.info("%s conversation closed %s", device, rec)
            ws.note(f"对话结束：{rec['start'][11:19]} 到 {rec['end'][11:19]}，{rec['sentences']} 句，参与 {'、'.join(rec['participants']) or '—'}", "state")
            await ws.broadcast({"type": "conversation", **db.conversation(rec["id"])})
        show_open()
        if live_ends:
            st.live = None
            await ws.broadcast({"type": "live", "device": device, "live": None})

    def present(o: dict) -> bool:
        """The device is recording now and o is the conversation going on: during a backlog the drafts of what is said
        now (open_view starts one of them after the gap), not the old conversation the final layer is still on."""
        edge = st.edge_end
        return bool(edge) and (dt.datetime.now() - edge).total_seconds() <= LIVE_AUDIO_S and \
            (edge - dt.datetime.fromisoformat(o["end"])).total_seconds() <= grouper.gap

    def live_due() -> bool:
        """A page is in front, the conversation going on has a minute of speech and is the present, and LIVE_EVERY_S of
        new speech came (or any, when a page just came to the front)."""
        o, lv, now = open_view(), st.live, time.time()
        if not o or o["speech_s"] < summary.MIN_SPEECH_S or now > T().watch_until or now - live_failed < 120 or not present(o):
            return False
        if not lv or not overlaps(lv, o):
            return True
        grown = o["speech_s"] - lv["speech_s"]
        return grown >= LIVE_EVERY_S or (grown > 0 and T().wake_at > lv["wall"])

    async def live_update(asked: bool = False):
        nonlocal live_failed
        o = open_view()
        if o is None:  # it closed between the start and now
            return
        lv = st.live if st.live and overlaps(st.live, o) else live.new(o["start"], device, db.conversations(o["start"][:10], o["start"]))
        rows, speech = [r for r in o["sentences"] if r.get("text")], o["speech_s"]
        try:
            res = await live.analyze(lv, rows)
        except Exception as e:
            live_failed = time.time()
            log.warning("%s live analysis failed: %r", device, e)
            ws.note(f"{device}：实时分析失败：{str(e)[:120]}，2 分钟后再试", "error")
            return
        res.update(speech_s=speech, wall=time.time(), at=dt.datetime.now().isoformat(timespec="seconds"),
                   upto=rows[-1]["start"], sentences=len(rows), asked=lv.get("asked") or asked)
        if (v := open_view()) and overlaps(res, v):
            st.live = res
            db.put_kv(f"live:{device}", res)
            await ws.broadcast({"type": "live", "device": device, "live": live.public(res)})
        elif r := db.conn().execute("SELECT id FROM conversations WHERE device = ? AND start <= ? AND end >= ? ORDER BY start DESC",
                                    (device, res["upto"], res["start"])).fetchone():
            db.put_timeline(r[0], res)  # it closed while the call ran

    async def analysis(asked: bool = False):
        try:
            await live_update(asked)
        except Exception:  # a bug here must not end the pipeline beside it (its task group would)
            log.exception("%s live analysis", device)
        finally:
            st.analyzing = False

    def start_analysis(asked: bool = False):
        """One at a time, in the pipeline's task group: it ends with the pipeline."""
        st.analyzing = True
        ptg.start_soon(analysis, asked, name=f"live:{device}")

    def maybe_live():
        if not st.analyzing and live_due():
            start_analysis()

    def ask() -> str | None:
        """立即总结: the live analysis of the conversation going on, now: before its minute of speech, before LIVE_EVERY_S
        more, during a failure's wait. One at a time, and only with speech it has not seen (the page holds its button 10 s
        after a press). None when it started, else why not. Asked once, its agent writes the final summary however short
        it stays (summarize)."""
        o, lv = open_view(), st.live
        if st.analyzing:
            return "[analyzing] 正在分析"
        if not o or not present(o):
            return "[no_open_conversation] 没有进行中的对话"
        if o["speech_s"] <= (lv["speech_s"] if lv and overlaps(lv, o) else 0):
            return "[no_new_speech] 没有新的语音"
        start_analysis(asked=True)
        return None

    def final_row(piece_s: float, sentences: int):
        perf.piece(device, "final", piece_s, sentences, fcost)
        if not st.drafting and dcost:  # no page watches, no draft passes: the draft layer's stream in a row of its own
            perf.piece(device, "draft", None, None, dcost)

    async def final_piece() -> bool:
        """Cut and finalize the next piece when there is one; True when a piece was done (look for the next at once)."""
        nonlocal owner
        if not grouper.open and T().owner is not None and T().owner is not owner:
            owner = T().owner  # 我 picked on the card (self_answer): from the next conversation on, never the open one
            dia.set_bank(owner)
            ddia.set_bank(owner)
        if not seg.anchors:
            return False
        done = st.done_to
        base = done or seg.time_at(seg.audio_base)
        st.utts = [u for u in st.utts if u[1] > base]
        rel = lambda t: (t - base).total_seconds()
        end_at = db.kv(f"end_at:{device}")
        force = bool(end_at) and not audio.queued(device)  # the recorder stopped and all its audio is in: no more is coming
        known = st.utts + ([(seg.time_at(seg.pending[0]), seg.time_at(seg.pending[1]))] if seg.pending else [])  # one waiting for a merge
        cut = next_cut([(max(0.0, rel(a)), rel(b)) for a, b in known], rel(seg.time_at(seg.samples)), seg.speaking(), force)
        if cut is None:
            return False
        a0 = max(seg.audio_base, seg.sample_at(base))
        b0 = min(seg.samples, seg.sample_at(base + dt.timedelta(seconds=cut)))
        if b0 <= a0:
            # finalized to the very end of a recording, the next one after a pause: the cut fell in the pause, no audio
            # to finalize. The pause has no speech: on to where the audio goes on (10-07: the iPhone's microphone waited
            # here from 17:59 on, 370 pieces queued; a restart started over the same way)
            if a0 < seg.samples and (nxt := seg.time_at(a0)) > base:
                await commit(grouper.advance(nxt), [], iso(base), nxt)
                return True
            return False
        x = seg.audio[a0 - seg.audio_base:b0 - seg.audio_base]
        at = lambda s: seg.time_at(a0 + round(s * SR))  # the piece may span a session boundary, where the clock jumps
        hi = at(len(x) / SR)
        # no speech: nothing to send, the clock still advances. Nor in less than a VAD frame (25 ms), whatever the VAD is
        # in at the newest audio: a store finalized to the ms before this fix left the last samples of a recording here
        if len(x) < SR // 40 or (not any(rel(u[0]) < cut for u in known) and not seg.speaking()):
            with perf.timed(fcost, "commit_ms"):
                await commit(grouper.advance(hi), [], iso(base), hi)  # no rows: pages still drop their drafts of this time
            final_row(len(x) / SR, 0)
            return True
        t_run = time.perf_counter()
        was_open = bool(grouper.open)
        m0 = dia.mark()
        try:
            with perf.timed(fcost, "asr_ms"):
                segs = await asr.engine_segs(x, speech_in(seg, a0, len(x)), device=device)
            engine = segs[0]["engine"] if segs else None
            with perf.timed(fcost, "embed_ms"):  # with music.py's YAMNet when on
                marks = await anyio.to_thread.run_sync(label_sentences, dia, embed, segs, x, state["music"], limiter=threads.INFER)
        except Exception as e:
            dia.rollback(m0)
            log.warning("%s final piece failed: %r", device, e)
            ws.note(f"{device}：定稿引擎调用失败：{str(e)[:120]}，稍后重试这一块", "error")
            st.refine = {"error": str(e)[:200]}
            await anyio.sleep(10)
            return False
        by_label = {}
        for g in segs:  # final_rows keys confidence by label
            by_label[g["speaker"]] = max(by_label.get(g["speaker"], 0.0), g["confidence"])
        lo, hi, rows = final_rows(segs, {g["speaker"]: g["speaker"] for g in segs}, base, len(x) / SR, by_label, 0.0, at, 0.0)
        st.voiced += [{"start": iso(at(g["start_ms"] / 1000)), "dur": (g["end_ms"] - g["start_ms"]) / 1000,
                          "e": dia.E[m[0] - 1] if m else None,  # music: no turn mates, so relabel keeps it S?
                          "key": (iso(base), g["engine_speaker"]) if g.get("engine_speaker") and not g.get("music") else None}
                         for g, m in zip(segs, marks)]
        with open(T().data / f"{lo.date()}.passes.jsonl", "a") as f:  # every piece's raw engine output, for tracing lost rows
            f.write(json.dumps({"at": dt.datetime.now().isoformat(timespec="seconds"), "layer": "final", "t0": iso(base), "since": 0.0,
                                "from": iso(lo), "to": iso(hi), "segs": segs}, ensure_ascii=False) + "\n")
        shown = dt.datetime.now()
        for r in rows:
            r["source"], r["engine"], r["shown_at"] = device, engine, iso(shown)
            st.final_latency.append(round((shown - dt.datetime.fromisoformat(r["end"])).total_seconds(), 1))
        del st.final_latency[:-10]
        closed = [c for r in rows for c in grouper.sentence(r)]
        closed += grouper.advance(hi)
        with perf.timed(fcost, "commit_ms"):
            await commit(closed, rows, iso(lo), hi)
        final_row(len(x) / SR, len(rows))
        st.refine = {"at": dt.datetime.now().isoformat(timespec="seconds"), "ms": round((time.perf_counter() - t_run) * 1000),
                        "engine": engine, "speakers": by_label, "changed": len(rows), "piece_s": round(len(x) / SR),
                        "backlog_s": round((seg.time_at(seg.samples) - hi).total_seconds())}
        log.info("final %s: %s", device, st.refine)
        ws.note(f"定稿一块：{device} {lo.strftime('%H:%M:%S')}–{hi.strftime('%H:%M:%S')}（{len(x) / SR:.0f} s）给 {engine or asr.ENGINE}，"
             f"{st.refine['ms'] / 1000:.1f} s，{len(rows)} 句，积压 {st.refine['backlog_s']} s")
        if segs and segs[0].get("fallback"):
            ws.note(f"{device}：定稿引擎失败（{segs[0]['fallback'][:120]}），这一块改用 {engine}", "warn")
        if grouper.open and not was_open:
            ws.note(f"{device}：对话开始 {grouper.open['start'][11:19]}", "state")
        return True

    async def final_loop():
        while True:
            while await final_piece():
                maybe_live()
            closed, was = [], grouper.open
            end_at = db.kv(f"end_at:{device}")
            final_all = not seg.anchors or (st.done_to and (seg.time_at(seg.samples) - st.done_to).total_seconds() < 0.5)
            if end_at and not audio.queued(device) and final_all:
                closed += grouper.end(dt.datetime.fromisoformat(end_at))  # the recorder stopped and its last audio is final
                db.put_kv(f"end_at:{device}", None)
            idle = grouper.tick()  # the safety net: nothing at all for IDLE_S (the recorder died without an end)
            if idle:
                ws.note(f"{device}：20 分钟没有任何事件，按兜底规则关闭对话", "warn")
            closed += idle
            if grouper.open is not was:  # closed, or dropped as too short (nothing closed): the store and pages too
                await commit(closed)
            maybe_live()
            await anyio.sleep(2)

    def drafting() -> bool:
        """A page watches and this device is recording now (its newest audio is recent, backlog or not)."""
        return (time.time() < T().watch_until and bool(dseg.anchors) and st.edge_end is not None
                and (dt.datetime.now() - st.edge_end).total_seconds() < LIVE_AUDIO_S)

    async def publish_drafts(rows: list[dict], upto: dt.datetime, engine: str):
        """Store, push and advance draft_to: a crash after this does not redo these rows."""
        shown = dt.datetime.now()
        for r in rows:
            r["id"], r["state"] = "d" + r["id"][1:], "draft"
            r["source"], r["engine"], r["shown_at"] = device, engine, iso(shown)
            st.latency.append(round((shown - dt.datetime.fromisoformat(r["end"])).total_seconds(), 1))
        del st.latency[:-10]
        with db.tx() as t:
            db.insert_drafts(t, rows)
            db.put_kv(f"draft_to:{device}", iso(upto), t)
        st.draft_to = upto
        done = st.done_to
        st.drafts += [r for r in rows if not done or r["start"] >= iso(done)]
        if rows:
            await ws.broadcast({"type": "draft", "device": device, "rows": rows})
        show_open()
        maybe_live()

    async def draft_pass(end: dt.datetime | None = None) -> bool:
        """One draft pass; True when more remains. Timer (end None): [draft_to - margin, + window], rows near its end
        left open; VAD: from 1 s before the first undrafted utterance to `end`, a point in the pause after one."""
        dto = st.draft_to
        t_run = time.perf_counter()
        if end is None:
            t0, x = dseg.window(REFINE_WINDOW_S, dto - dt.timedelta(seconds=REFINE_MARGIN_S))
        else:
            us = st.draft_utts = [u for u in st.draft_utts if u[1] > dto]
            if not us:
                return False
            start = max(us[0][0] - dt.timedelta(seconds=1), dto - dt.timedelta(seconds=REFINE_MARGIN_S))
            t0, x = dseg.window(min(REFINE_WINDOW_S, (dseg.sample_at(end) - dseg.sample_at(start)) / SR), start)
        a0 = dseg.sample_at(t0)
        at = lambda s: dseg.time_at(a0 + int(s * SR))
        since = max(0.0, (dseg.sample_at(dto) - a0) / SR)
        edge = 0.0 if end is not None and len(x) / SR < REFINE_WINDOW_S else EDGE_S  # a window cut short of its pause may end mid-sentence
        if len(x) / SR - since < (0.3 if end is not None else EDGE_S + 10):
            return False
        hi = at(len(x) / SR - EDGE_S)
        if end is None and not dseg.speaking() and (st.last_speech is None or st.last_speech <= dto):
            st.draft_to = hi  # silence: nothing to send
            db.put_kv(f"draft_to:{device}", iso(hi))
            return (dseg.time_at(dseg.samples) - hi).total_seconds() > REFINE_WINDOW_S - REFINE_MARGIN_S
        sent, n_rows = since, 0  # seconds into the window up to which rows went out while the call ran

        async def publish(rows: list[dict], upto: dt.datetime, engine: str):
            nonlocal n_rows
            with perf.timed(dcost, "commit_ms"):
                await publish_drafts(rows, upto, engine)
            n_rows += len(rows)

        async def early(segs: list[dict]):
            """An answer during the call: its sentences but the last (it may still grow) go out now, labelled in order."""
            nonlocal sent
            segs = clip_turns(segs[:-1], sent)
            if not segs:
                return
            with perf.timed(dcost, "embed_ms"):
                await anyio.to_thread.run_sync(label_sentences, ddia, embed, segs, x, limiter=threads.INFER)
            rows = final_rows(segs, {g["speaker"]: g["speaker"] for g in segs}, t0, len(x) / SR, None, sent, at, 0.0)[2]
            sent = segs[-1]["end_ms"] / 1000
            await publish(rows, at(sent), segs[0]["engine"])

        send, answers = anyio.create_memory_object_stream(math.inf)
        got = []

        async def call():
            with send, perf.timed(dcost, "asr_ms"):  # closed when the call ends, and with it the answers
                got.append(await asr.engine_segs(x, speech_in(dseg, a0, len(x)), send.send_nowait, asr.DRAFT_ENGINE, "draft", device))
        m0 = ddia.mark()
        try:
            with answers:
                async with anyio.create_task_group() as tg:  # a pass failing or cancelled takes its engine call with it
                    tg.start_soon(call)
                    async for segs in answers:  # a 5 min backfill window: its first sentences in ~3 s, not after the whole call
                        await early(segs)
                        m0 = ddia.mark()
            raw = got[0]
            segs = clip_turns(raw, sent)
            with perf.timed(dcost, "embed_ms"):
                marks = await anyio.to_thread.run_sync(label_sentences, ddia, embed, segs, x, limiter=threads.INFER)
        except Exception as e:
            e = threads.first(e)
            ddia.rollback(m0)
            log.warning("%s draft pass failed: %r", device, e)
            st.draft = {"error": str(e)[:200]}
            return False
        by_label = {}
        for g in segs:
            by_label[g["speaker"]] = max(by_label.get(g["speaker"], 0.0), g["confidence"])
        lo, hi, rows = final_rows(segs, {g["speaker"]: g["speaker"] for g in segs}, t0, len(x) / SR, by_label, sent, at, edge)
        kept = [m for g, m in zip(segs, marks) if m and at(g["start_ms"] / 1000) < hi]  # sentences left open come back next pass
        ddia.rollback(kept[-1] if kept else m0)
        hi = max(hi, at(sent))  # rows that went out early near the window's end: never draft them twice
        with open(T().data / f"{lo.date()}.passes.jsonl", "a") as f:
            f.write(json.dumps({"at": dt.datetime.now().isoformat(timespec="seconds"), "layer": "draft", "t0": iso(t0), "since": since,
                                "early_to": sent, "from": iso(at(since)), "to": iso(hi), "segs": raw}, ensure_ascii=False) + "\n")
        engine = raw[0]["engine"] if raw else asr.DRAFT_ENGINE
        await publish(rows, hi, engine)
        perf.piece(device, "draft", len(x) / SR, n_rows, dcost)
        st.draft = {"at": dt.datetime.now().isoformat(timespec="seconds"), "ms": round((time.perf_counter() - t_run) * 1000),
                       "engine": engine, "changed": n_rows, "early_s": round(sent - since), "to": iso(hi)}
        log.info("draft %s: %s", device, st.draft)
        if end is not None:  # pauses that came during the pass have set wake again
            return len(x) / SR >= REFINE_WINDOW_S
        return (dseg.time_at(dseg.samples) - hi).total_seconds() > REFINE_WINDOW_S - REFINE_MARGIN_S

    async def draft_loop():
        while True:
            with anyio.move_on_after(REFINE_EVERY_S):  # a pause after speech drafts at once; the timer covers pause-less speech
                await st.wake.wait()
            by_vad = st.take_draft_wake()
            if not drafting():
                st.drafting = False
                continue
            # from where drafts or final rows stop, but no further back than DRAFT_BACK_S: a page that opens sees the
            # minutes not final yet, not a backlog
            floor = dseg.time_at(dseg.samples) - dt.timedelta(seconds=DRAFT_BACK_S)
            st.draft_to = max(t for t in (st.draft_to, st.done_to, floor, dseg.time_at(dseg.audio_base)) if t)
            if not st.drafting:
                st.drafting = True
                st.latency.clear()  # counted afresh: the rows of an earlier watch say nothing of this one
                ws.note(f"{device}：页面在看，开始出初稿（从 {st.draft_to.strftime('%H:%M:%S')} 起）", "state")
                while await draft_pass():  # what came before the page opened, in windows
                    pass
            if by_vad:
                while st.vad_to and st.vad_to > st.draft_to and await draft_pass(st.vad_to):
                    pass
            else:
                while await draft_pass():
                    pass

    async def stream_sentence(g: dict, base: int):
        """A sentence of the streaming session that began at stream sample `base`: one still being said goes to the
        pages as `partial` (replaced by the next one); one it ended becomes a draft row, labelled as a draft pass's."""
        a = base + g["start_ms"] * SR // 1000
        b = max(a, base + g["end_ms"] * SR // 1000)
        at = lambda s: dseg.time_at(a + int(s * SR))
        if not g["final"]:
            await ws.broadcast({"type": "partial", "device": device, "row": {
                "id": "p" + device, "start": at(0).isoformat(timespec="milliseconds"), "end": at((b - a) / SR).isoformat(timespec="milliseconds"),
                "speaker": "", "text": g["text"], "state": "draft", "source": device, "engine": "qwen-stream"}})
            return
        seg = {"start_ms": 0, "end_ms": (b - a) * 1000 // SR, "text": g["text"], "speaker": None}
        x = dseg.audio[max(0, a - dseg.audio_base):max(0, b - dseg.audio_base)]
        await anyio.to_thread.run_sync(label_sentences, ddia, embed, [seg], x, limiter=threads.INFER)
        rows = final_rows([seg], {seg["speaker"]: seg["speaker"]}, at(0), (b - a) / SR, None, 0.0, at, 0.0)[2]
        await publish_drafts(rows, at((b - a) / SR), "qwen-stream")
        st.draft = {"at": dt.datetime.now().isoformat(timespec="seconds"), "engine": "qwen-stream", "to": iso(st.draft_to)}

    async def stream_loop():
        """DRAFT_ENGINE qwen-stream: while drafting(), one streaming session (qwen_stream) fed the draft stream's audio as
        it comes, every 0.2 s, from where drafts or final rows stop (as draft_loop); each sentence it ends is a draft row
        at once, the one still being said is shown as `partial`. A session that ends (the page went, the server closed
        it, it failed) is one call in the calls table; the next starts again at the end of the last draft row."""
        while True:
            await anyio.sleep(1)
            if not drafting():
                st.drafting = False
                continue
            floor = dseg.time_at(dseg.samples) - dt.timedelta(seconds=DRAFT_BACK_S)
            st.draft_to = max(t for t in (st.draft_to, st.done_to, floor, dseg.time_at(dseg.audio_base)) if t)
            if not st.drafting:
                st.drafting = True
                ws.note(f"{device}：页面在看，开始出初稿（实时流，从 {st.draft_to.strftime('%H:%M:%S')} 起）", "state")
            base = max(dseg.audio_base, dseg.sample_at(st.draft_to))
            sent, t, err = base, time.perf_counter(), None
            st.latency.clear()  # counted afresh: the last session's rows (an earlier watch, a timeout) say nothing of this one
            try:
                async with qwen_stream.Session() as s, anyio.create_task_group() as tg:
                    async def pump():
                        nonlocal sent
                        while drafting():
                            if dseg.samples > sent:
                                lo, sent = max(sent, dseg.audio_base), dseg.samples
                                await s.send(dseg.audio[lo - dseg.audio_base:sent - dseg.audio_base].tobytes())
                            await anyio.sleep(0.2)
                        await s.finish()
                    tg.start_soon(pump)
                    async for g in s.results():
                        await stream_sentence(g, base)
                    tg.cancel_scope.cancel()  # the task finished: no more audio into it
            except Exception as e:
                err = threads.first(e)
                log.warning("%s draft stream failed after %.0f s: %r", device, time.perf_counter() - t, err)
                st.draft = {"error": str(err)[:200]}
            db.add_call("draft", "qwen-stream", device, round((time.perf_counter() - t) * 1000), err and asr.failure(err), (sent - base) / SR, (sent - base) / SR)
            await ws.broadcast({"type": "partial", "device": device, "row": None})

    async def forever(part):
        await part()
        raise RuntimeError(f"{part.__name__} returned")

    # each part runs for good: the first to end (an exception) ends the pipeline, its task group taking the others and
    # the live analysis with it (nothing left behind: two readers on one device would both move its queue), and
    # lasting() starts it again whole
    try:
        async with anyio.create_task_group() as ptg:
            st.ask = ask
            drafts = stream_loop if asr.DRAFT_ENGINE == "qwen-stream" else draft_loop
            for name, part in (("feed", feed), ("edge", edge_feed), ("draft", drafts), ("final", final_loop)):
                ptg.start_soon(forever, part, name=f"{name}:{device}")
    finally:
        st.ask = None  # no task group to run in until lasting() starts it again
        T().dias.remove(dia)
        T().dias.remove(ddia)
