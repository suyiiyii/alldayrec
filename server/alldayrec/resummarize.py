"""Redo the summaries of the last days with the current prompt, through the running server's own path (POST
/api/conversation/{id}/summarize, as 重新生成 does), one conversation at a time: the backfill after a prompt change.

    cd server && uv run python -m alldayrec.resummarize [--days 30] [--delay 5] [--limit N] [--dry-run]

Run it on the server's host (the calls ledger it reads for 429s is by the server's clock). The token: LIVE_RECORD_TOKEN,
else the first line on stdin (never an argument). --limit redoes only the first N (oldest first): one, a few, then all. A conversation started in the last
--days days (today included) is redone unless its summary is on summary.PROMPT_VERSION already (a second run resumes
where the first stopped), is still pending, or has under MIN_SPEECH_S of speech (no model call: its title is its first
sentence, and a redo would drop what 立即总结 wrote). Each redo is waited for (the server keeps the old summary until
the new one is written, and keeps it when every try fails) before the next, then --delay seconds; a model 429 in the
calls since (GET /api/calls) doubles the delay, up to --max-delay, and a redo that went through brings it back.
A timeout or uncertain HTTP result stops the run (the server may still finish it): rerun after checking it.
A failed upgrade leaves its error on the old summary; it counts failed and the run continues. HTTP 429s also back off.

Prints a line per conversation, then the totals: redone, failed, skipped; todos and marked todos before and after;
the model calls the server made meanwhile by kind (summary; hints and profile calls follow some summaries; anything
else the server did in that time is counted too).
"""
import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from alldayrec.summary import MIN_SPEECH_S, PROMPT_VERSION


def client(url: str, token: str, delay: float = 5, max_delay: float = 600):
    def call(method: str, path: str, **params):
        q = urllib.parse.urlencode(params)
        req = urllib.request.Request(url.rstrip("/") + path + (f"?{q}" if q else ""), method=method,
                                     headers={"Authorization": "Bearer " + token} if token else {})
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                if e.code != 429 or attempt == 3:
                    raise
                try:
                    retry = float(e.headers.get("Retry-After", "0"))
                except ValueError:
                    retry = 0
                time.sleep(min(max_delay, max(retry, max(delay, 1) * 2 ** attempt)))
    return call


def todos(s: dict) -> tuple[int, int]:
    """A summary's todos, and how many of them are marked 完成 or 不对 (GET /api/conversation gives each its mark)."""
    ts = (s or {}).get("todos") or []
    return len(ts), sum(1 for t in ts if t.get("mark"))


def due(c: dict) -> str | None:
    """Why a conversation is left as it is, None when it is to be redone."""
    s = c.get("summary") or {}
    if c.get("state") != "closed":
        return "open"
    if (c.get("speech_s") or 0) < MIN_SPEECH_S:
        return "short"
    if s.get("status") == "pending":
        return "pending"
    if s.get("status") == "done" and (s.get("prompt_version") or 0) >= PROMPT_VERSION:
        return "current"
    return None


def calls(api, since: str) -> dict[str, dict]:
    """The model calls since then by kind: {kind: {n, failed: {code: n}}}."""
    out = {}
    for x in api("GET", "/api/calls", since=since)["calls"]:
        if x["kind"] in ("summary", "split", "hints", "profile", "recap", "live", "ask"):
            k = out.setdefault(x["kind"], {"n": 0, "failed": {}})
            k["n"] += x["n"]
            for code, n in x["failed"].items():
                k["failed"][code] = k["failed"].get(code, 0) + n
    return out


def redo(api, c: dict, timeout: float, poll: float = 3.0) -> tuple[dict | None, bool]:
    """POST the redo and wait: (the conversation as it is then, whether the model answered 429 meanwhile). The first is
    None when this attempt failed; uncertain completion at the timeout raises TimeoutError."""
    since = dt.datetime.now().isoformat(timespec="seconds")
    api("POST", f"/api/conversation/{c['id']}/summarize")
    end, hit429 = time.monotonic() + timeout, False
    while time.monotonic() < end:
        time.sleep(poll)
        x = api("GET", f"/api/conversation/{c['id']}")
        s = x.get("summary") or {}
        failed = calls(api, since).get("summary", {}).get("failed", {})
        hit429 = hit429 or "429" in failed
        # POST resets a failed old row to pending; a failure observed here belongs to this retry.
        if s.get("error") or s.get("status") == "failed":
            return None, hit429
        if s.get("status") == "done" and (s.get("prompt_version") or 0) >= PROMPT_VERSION:
            return x, hit429
    raise TimeoutError("summary still pending; stopping before another conversation is queued")


def main(argv: list[str] | None = None) -> dict:
    p = argparse.ArgumentParser(prog="python -m alldayrec.resummarize", description=__doc__.split("\n\n")[0])
    p.add_argument("--days", type=int, default=30, help="conversations started in the last N days, today included (30)")
    p.add_argument("--delay", type=float, default=5.0, help="seconds between two redos (5)")
    p.add_argument("--max-delay", type=float, default=600.0, help="the most a 429 doubles the delay to (600)")
    p.add_argument("--timeout", type=float, default=1800.0, help="seconds to wait for one redo (1800)")
    p.add_argument("--limit", type=int, default=0, help="redo at most N, the oldest first (0: all)")
    p.add_argument("--dry-run", action="store_true", help="list what would be redone, change nothing")
    a = p.parse_args(argv)
    if a.days < 1 or a.limit < 0 or a.delay < 0 or a.max_delay < max(a.delay, 1) or a.timeout <= 0:
        p.error("days/timeout must be positive, limit/delay nonnegative, max-delay >= max(delay, 1)")
    url = os.environ.get("LIVE_RECORD_URL") or sys.exit("LIVE_RECORD_URL is not set")
    token = os.environ.get("LIVE_RECORD_TOKEN") or ("" if sys.stdin.isatty() else sys.stdin.readline().strip())
    api = client(url, token, a.delay, a.max_delay)

    today = dt.date.today()
    convs = [c for d in range(a.days - 1, -1, -1) for c in api("GET", "/api/conversations", date=(today - dt.timedelta(days=d)).isoformat())]
    skipped = {}
    todo = []
    for c in convs:
        why = due(c)
        if why:
            skipped[why] = skipped.get(why, 0) + 1
        else:
            todo.append(c)
    todo = todo[:a.limit or None]
    print(f"{len(convs)} conversations since {today - dt.timedelta(days=a.days - 1)}: {len(todo)} to redo to prompt v{PROMPT_VERSION}, "
          f"skipped {json.dumps(skipped)}", flush=True)
    if a.dry_run:
        for c in todo:
            print(f"  {c['id']} {c['start'][5:16].replace('T', ' ')} v{(c.get('summary') or {}).get('prompt_version')} "
                  f"{(c.get('summary') or {}).get('title') or ''}")
        return {"todo": len(todo), "skipped": skipped}

    run_start = dt.datetime.now().isoformat(timespec="seconds")
    tot = {"redone": 0, "failed": 0, "todos": [0, 0], "marked": [0, 0], "lost_marks": []}
    delay = a.delay
    for i, c in enumerate(todo, 1):
        t, stop = time.monotonic(), False
        before = c
        try:
            before = api("GET", f"/api/conversation/{c['id']}")
            after, hit429 = redo(api, c, a.timeout)
        except OSError as e:  # acceptance/completion is unknown: stop to keep the run sequential
            after, hit429, stop = None, False, True
            print(f"  {c['id']}: {type(e).__name__}; stopping (completion unknown)", flush=True)
        n0, m0 = todos(before.get("summary"))
        try:
            current = after or api("GET", f"/api/conversation/{c['id']}")
        except OSError:
            current, stop = before, True
            print("  current counts unavailable; retaining last observed counts", flush=True)
        n1, m1 = todos(current.get("summary"))
        tot["todos"][0] += n0
        tot["todos"][1] += n1
        tot["marked"][0] += m0
        tot["marked"][1] += m1
        head = f"[{i}/{len(todo)}] {c['id']} v{(before.get('summary') or {}).get('prompt_version')}"
        if after:
            tot["redone"] += 1
            if m1 < m0:
                tot["lost_marks"].append(c["id"])
            print(f"{head} -> v{PROMPT_VERSION} {time.monotonic() - t:.0f} s  todos {n0}->{n1}  marked {m0}->{m1}  "
                  f"{(after['summary'].get('summary') or '')[:60]}", flush=True)
        else:
            tot["failed"] += 1
            print(f"{head} FAILED after {time.monotonic() - t:.0f} s", flush=True)
        if stop:
            break
        delay = min(max(delay, 1) * 2, a.max_delay) if hit429 else a.delay if after else delay
        if i < len(todo):
            time.sleep(delay)
    try:
        tot["calls"] = calls(api, run_start)
    except OSError:
        tot["calls"] = {"unavailable": True}
    print(f"redone {tot['redone']}, failed {tot['failed']}, skipped {json.dumps(skipped)}; todos {tot['todos'][0]} -> {tot['todos'][1]}, "
          f"marked {tot['marked'][0]} -> {tot['marked'][1]}" + (f" (fewer in {', '.join(tot['lost_marks'])})" if tot["lost_marks"] else "")
          + f"; model calls meanwhile {json.dumps(tot['calls'])}", flush=True)
    return tot


if __name__ == "__main__":
    sys.exit(1 if main().get("failed") else 0)
