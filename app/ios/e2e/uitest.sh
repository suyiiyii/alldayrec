#!/bin/bash
# The iPhone + recorder test, the hands taken over by XCUITest (../RunnerUITests): the taps in the app, iOS's
# permission and pairing dialogs, Settings › Bluetooth (listed? forget it, Bluetooth off and on), home, quit. It runs a
# test app next to the real one (com.suyiiyii.liverecord.uitest, "alldayrec测试": its own settings, binding and queue;
# the real app and its recorder are never opened or changed) against a throwaway server (server.py behind a cloudflared
# quick tunnel). Each step is judged from what that server got (status reports, app events, audio) and from the
# recorder's adverts as this Mac sees them (ble.py), into $W/<time>/summary.tsv, printed as a table at the end.
#   app/ios/e2e/uitest.sh [--build] [--fake] [STEP...]
#     --build  build the test app (release: debug Flutter builds do not run on their own) and the UI tests; install
#     --fake   this Mac is the recorder (fake.swift) instead of LR-P1: no pairing, no adverts seen here, the steps that
#              need a real board say SKIP (a dry run of the plumbing and the screens)
#   steps (default: all, in this order): setup add listed live pause away background quit kill kicked readd unbind finish
#   (stop: only the helpers)
# The person: unlock the iPhone once (setup sets Auto-Lock to 永不 for the run, finish puts the old one back); if this
# Mac asks to pair with LR-P1 (kicked), click Pair. Afterwards: forget LR-P1 in this Mac's Bluetooth settings (LR-P1
# forgets the Mac in readd, the Mac keeps stale keys). `away` turns the iPhone's Bluetooth off for
# 2 min: the real app loses its recorder meanwhile (the recorder records to its card, the app catches up after).
# Env: LR_DEVICE (the iPhone), LR_NAME (the recorder, default LR-P1-BDAE), LOCK=1 (background: the lock button instead
# of home; the phone then needs a second unlock). Needs Xcode, flutter, ffmpeg, cloudflared, uv; LR-P1 not held by
# another phone, its card backlog cleared, this Mac without a bond to it. The UI tests build and run with XCODE_UI
# (default Xcode 27: the iPhone runs iOS 27, which Xcode 26.6's XCTest cannot drive, 10-05); everything else uses the
# selected Xcode.
set -uo pipefail
XCODE_UI=${XCODE_UI:-/Applications/Xcode-27.app/Contents/Developer}
cd "$(dirname "$0")"
E=$PWD
W=${TMPDIR:-/tmp}/lr-uitest; R=$W/$(date +%m%d-%H%M); mkdir -p "$R"
APP=com.suyiiyii.liverecord.uitest
DEV=${LR_DEVICE:-73D21723-EAD4-543D-A190-746CA7BCD76A}  # SuPhone17
NAME=${LR_NAME:-LR-P1-BDAE} FAKE= BUILD=
while [ "${1:-}" = --build ] || [ "${1:-}" = --fake ]; do
  [ "$1" = --build ] && BUILD=1; [ "$1" = --fake ] && { FAKE=1; NAME=LR-MAC-BEEF; }; shift
done
STEPS=${*:-setup add listed live pause away background quit kill kicked readd unbind finish}

log() { echo "$(date +%T) $*" | tee -a "$R/run.log"; }
verdict() { printf '%s\t%s\t%s\n' "$1" "$2" "$3" >> "$R/summary.tsv"; log "== $1 $2: $3"; }
ms() { python3 -c 'import time; print(int(time.time() * 1000))'; }
ev() { python3 "$E/evidence.py" "$W/up" "$@" 2>&1 | tee -a "$R/run.log"; return "${PIPESTATUS[0]}"; }
ble() { uv run -q --no-project --with bleak python "$E/ble.py" "$@" 2>&1 | tee -a "$R/run.log"; return "${PIPESTATUS[0]}"; }
real() { [ -z "$FAKE" ] || { verdict "$1" SKIP "needs a real board (--fake)"; return 1; }; }

unlocked() {
  xcrun devicectl device info lockState --device "$DEV" 2>/dev/null | grep -q "passcodeRequired: false" && return
  log "OWNER: unlock the iPhone"
  while ! xcrun devicectl device info lockState --device "$DEV" 2>/dev/null | grep -q "passcodeRequired: false"; do sleep 3; done
}
N=0
ui() {  # one test of RunnerUITests (testLaunch: ui Launch), VAR=value for it; its LRUI lines into run.log
  local t=$1; shift; local vars=(TEST_RUNNER_LR_NAME="$NAME" TEST_RUNNER_LR_REAL="$([ -z "$FAKE" ] && echo 1)")
  for v in "$@"; do vars+=("TEST_RUNNER_$v"); done
  local try rc
  for try in 1 2 3; do
    unlocked; runner_gone; N=$((N + 1))
    env "${vars[@]}" DEVELOPER_DIR="$XCODE_UI" xcodebuild test-without-building -xctestrun "$(ls "$W"/dd/Build/Products/*iphoneos*.xctestrun)" \
      -destination "id=$DEV" -only-testing:"RunnerUITests/Flow/test$t" -resultBundlePath "$R/$N-$t.xcresult" > "$R/$N-$t.log" 2>&1 &
    local x=$! k=$SECONDS
    # a runner that cannot reach xcodebuild exits within ~20 s, xcodebuild then waits on for 10 min (10-06): try again
    while kill -0 $x 2>/dev/null; do
      sleep 3
      if grep -q "Exiting due to IDE disconnection" "$R/$N-$t.log" || [ $((SECONDS - k)) -gt 900 ]; then kill $x 2>/dev/null; break; fi
    done
    wait $x; rc=$?
    grep -q "Exiting due to IDE disconnection" "$R/$N-$t.log" || break
    log "ui $t: the runner did not reach xcodebuild (try $try)"; rc=1; sleep 10
  done
  grep -h "^LRUI\|error: -\[RunnerUITests" "$R/$N-$t.log" | tee -a "$R/run.log"
  log "ui $t: $([ $rc = 0 ] && echo passed || echo "FAILED (log $R/$N-$t.log)")"
  return $rc
}
runner_gone() {  # no test runner left from the last test (the next one cannot connect while it lingers?)
  xcrun devicectl device info processes --device "$DEV" --json-output "$W/procs.json" >/dev/null 2>&1
  python3 -c "import json,sys; print(*[p['processIdentifier'] for p in json.load(open(sys.argv[1]))['result']['runningProcesses'] if 'RunnerUITests-Runner.app/' in p.get('executable', '')])" \
    "$W/procs.json" 2>/dev/null | xargs -n1 -I{} xcrun devicectl device process terminate --device "$DEV" --pid {} >/dev/null 2>&1
  return 0
}
pid() {  # the test app's pid, if it runs
  xcrun devicectl device info apps --device "$DEV" --bundle-id $APP --json-output "$W/apps.json" >/dev/null 2>&1
  xcrun devicectl device info processes --device "$DEV" --json-output "$W/procs.json" >/dev/null 2>&1
  python3 -c "
import json,sys; u=json.load(open(sys.argv[1]))['result']['apps'][0]['url']
print(next((p['processIdentifier'] for p in json.load(open(sys.argv[2]))['result']['runningProcesses'] if p.get('executable','') == u + 'Runner'), ''))" \
    "$W/apps.json" "$W/procs.json" 2>/dev/null
}
settings() {  # the app's settings for testLaunch: the test server, and $1 (more JSON keys)
  python3 -c 'import json,sys; s,t,more=sys.argv[1:]; d={"server": s, "token": t, "device": "iphone-uitest", "archive": False,
    "foreground": 1, "background": 20, "flutter.server": s, "flutter.token": t, "flutter.archive": False}; d.update(json.loads(more)); print(json.dumps(d))' \
    "$(cat "$W/url")" "$(cat "$W/token")" "$1"
}
IDLE='{"source": "mic", "active": false, "flutter.source": "mic"}'  # bound or not, the link stays off
LINK='{"source": "ble", "active": true, "flutter.source": "ble"}'
free() {  # the recorder advertising connectable (no phone holds it) within $1 s
  for _ in $(seq $(( ${1:-30} / 5 ))); do tail -3 "$W/adv.jsonl" 2>/dev/null | grep -q '"connectable": true' && return 0; sleep 5; done
  return 1
}
state() {  # the last state byte it advertised (scan responses carry it)
  tail -50 "$W/adv.jsonl" 2>/dev/null | python3 -c 'import json,sys; print(next((x["state"] for x in map(json.loads, reversed(sys.stdin.read().split("\n")[:-1])) if x["state"] is not None), None))'
}
gaps() { ev audio "$NAME" "$1" "$2" | grep -o 'gaps: .*' | grep -o '(' | wc -l | tr -d ' '; }

step_setup() {  # build/install if asked, server + tunnel, adverts watched (or the fake up), the test app on it, unbound
  if [ -n "$BUILD" ]; then
    (cd ../.. && FLUTTER_XCODE_LR_TEST_ID=.uitest FLUTTER_XCODE_LR_TEST_NAME=测试 flutter build ios --release) > "$R/build.log" 2>&1 \
      || { verdict setup FAIL "app build (build.log)"; exit 1; }
    local id; id=$(/usr/libexec/PlistBuddy -c "Print CFBundleIdentifier" ../../build/ios/iphoneos/Runner.app/Info.plist)
    [ "$id" = $APP ] || { verdict setup FAIL "built $id, not $APP: not installed"; exit 1; }  # never over the real app
    xcrun devicectl device install app --device "$DEV" ../../build/ios/iphoneos/Runner.app >> "$R/build.log" 2>&1 \
      || { verdict setup FAIL "install (build.log)"; exit 1; }
    DEVELOPER_DIR="$XCODE_UI" xcodebuild build-for-testing -project ../Runner.xcodeproj -scheme RunnerUITests -destination generic/platform=iOS \
      -derivedDataPath "$W/dd" -allowProvisioningUpdates >> "$R/build.log" 2>&1 || { verdict setup FAIL "UI test build (build.log)"; exit 1; }
  fi
  ls "$W"/dd/Build/Products/*iphoneos*.xctestrun >/dev/null 2>&1 || { verdict setup FAIL "no UI test build: --build"; exit 1; }
  local port; port=$((40000 + RANDOM % 20000)); openssl rand -hex 16 > "$W/token"
  rm -rf "${W:?}/up"
  nohup python3 "$E/server.py" serve 127.0.0.1 "$port" "$(cat "$W/token")" "$W/up" > "$W/server.log" 2>&1 & echo $! > "$W/server.pid"
  nohup cloudflared tunnel --no-autoupdate --url "http://127.0.0.1:$port" > "$W/tunnel.log" 2>&1 & echo $! > "$W/tunnel.pid"
  local url=
  for _ in $(seq 60); do
    url=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$W/tunnel.log" | head -1)
    [ -n "$url" ] && curl -sf -m 10 -H "Authorization: Bearer $(cat "$W/token")" "$url/api/client-config" >/dev/null && break; sleep 2
  done
  [ -n "$url" ] || { verdict setup FAIL "the tunnel does not come up"; exit 1; }
  echo "$url" > "$W/url"; log "test server $url"
  if [ -n "$FAKE" ]; then
    swiftc -O -o "$W/fake" fake.swift || { verdict setup FAIL "fake.swift"; exit 1; }
    [ -f "$W/tone.ogg" ] || ffmpeg -v error -f lavfi -i "aevalsrc='0.3*sin(2*PI*(300+100*mod(floor(t)\,10))*t)':s=16000:d=120" \
      -ac 1 -c:a libopus -frame_duration 10 -b:a 24k "$W/tone.ogg"
    rm -rf "${W:?}/card"; (cd "$W" && exec ./fake $NAME > fake.log 2>&1) & echo $! > "$W/fake.pid"
  else
    : > "$W/adv.jsonl"
    nohup uv run -q --no-project --with bleak python "$E/ble.py" watch $NAME 7200 "$W/adv.jsonl" > "$W/watch.log" 2>&1 &
    sleep 15
    free 20 || log "$NAME is held by another phone or computer (state $(state)): it has to let go before add"
  fi
  if [ ! -s "$W/autolock" ]; then  # the phone's own Auto-Lock, kept until finish puts it back
    ui AutoLock LR_AUTOLOCK=永不 && sed -n 's/^LRUI autolock was //p' "$R/$N-AutoLock.log" | head -1 > "$W/autolock"
    [ -s "$W/autolock" ] && log "Auto-Lock was $(cat "$W/autolock"), now 永不 until finish" || log "OWNER: set Auto-Lock to 永不 (Settings › 显示与亮度) until the end"
  fi
  ui Launch LR_E2E="$(settings '{"source": "mic", "active": false, "flutter.source": "mic", "bleName": null, "bleId": null,
    "bindPending": null, "bindForce": null, "bleKicked": null, "bleModel": null, "bleFw": null}')" \
    && verdict setup PASS "server up, test app on it and unbound; $NAME state $(state)" || { verdict setup FAIL "the test app did not start"; exit 1; }
}

step_add() {  # the add list, force-binding, iOS's pairing dialog, following live
  local t; t=$(ms)
  if [ -n "$FAKE" ]; then  # a Mac advertises no full name (28 bytes: the service UUID and an 8-character name at most)
    fake_bind || { verdict add FAIL "the app did not take the fake"; return; }
  else
    ui Add LR_WAIT_S=300 || { verdict add FAIL "no 已连接 · 实时上传 in the app's status sheet (ui log)"; return; }
    grep -q "^LRUI bound force" "$R/run.log" || log "it was not bound to another phone: 绑定, not 强制绑定"
  fi
  ev wait-status "$NAME" "$t" 120 && verdict add PASS "bound, $NAME reports through the iPhone" || verdict add FAIL "no status report from $NAME"
  ev wait-audio "$NAME" "$t" "$(( $(ms) - 5000 ))" 600 >/dev/null || log "the card's backlog is still coming in"
}

fake_bind() {  # the fake by its identifier, as run.sh: the app logs what it sees while scanning for a name it never gets
  # first through XCUITest: a new test app asks for Bluetooth on its first scan, and only a test can say Allow (10-06)
  ui Launch LR_E2E="$(settings "${LINK%\}}, \"bleName\": \"$NAME\", \"bleId\": null}")" || return 1
  local n; n=$(wc -l < "$W/console.log" 2>/dev/null || echo 0)
  xcrun devicectl device process launch --device "$DEV" --terminate-existing --console --environment-variables \
    "$(python3 -c 'import json,sys; print(json.dumps({"LR_E2E": sys.argv[1]}))' "$(settings "${LINK%\}}, \"bleName\": \"$NAME\", \"bleId\": null}")")" \
    $APP >> "$W/console.log" 2>&1 &
  local c=$! id=
  for _ in $(seq 15); do
    sleep 2
    id=$(tail -n +$((n + 1)) "$W/console.log" | sed -n 's/.*live-record ble scan: - (advertised no name) \([0-9A-F-]\{36\}\).*/\1/p' | head -1)
    [ -n "$id" ] && break
  done
  kill $c 2>/dev/null
  [ -n "$id" ] || return 1
  log "the fake is $id on the phone"
  ui Launch LR_E2E="$(settings "${LINK%\}}, \"bleName\": \"$NAME\", \"bleId\": \"$id\", \"bindPending\": true, \"bindForce\": true}")" && ui Open LR_WAIT_S=60
}

step_listed() {  # Settings › Bluetooth lists it (the 忽略此设备 advice needs that)
  if [ -n "$FAKE" ]; then  # the screens only, read-only: the real recorder paired for the real app
    NAME=LR-X1-567E ui Listed && verdict listed SKIP "fake: Settings › Bluetooth read (LR-X1-567E listed)" \
      || verdict listed SKIP "fake: Settings › Bluetooth not read (ui log)"
    return
  fi
  ui Listed && verdict listed PASS "$NAME under 我的设备 in Settings › Bluetooth" || verdict listed FAIL "$NAME not listed (ui log: the screen)"
}

step_live() {  # 3 min followed live, gap-free
  local a; a=$(ms); sleep 180
  ev wait-audio "$NAME" "$a" "$(( a + 170000 ))" 300 && verdict live PASS "3 min live, gap-free" || verdict live FAIL "gaps or missing audio (above)"
}

step_pause() {  # the app's pause for a minute and 继续: one gap that long, the events
  local t; t=$(ms)
  ui Pause LR_PAUSE_S=60 || { verdict pause FAIL "the page did not pause or go on (ui log)"; return; }
  ev wait-event pause "$t" 60 && ev wait-event resume "$t" 60 || { verdict pause FAIL "no pause/resume events"; return; }
  sleep 60
  ev wait-audio "$NAME" "$t" "$(( $(ms) - 30000 ))" 300 >/dev/null
  local n; n=$(gaps "$t" "$(( $(ms) - 30000 ))")
  [ "$n" = 1 ] && verdict pause PASS "one gap, the pause (above)" || verdict pause FAIL "$n gaps (one expected)"
}

step_away() {  # Bluetooth off for 2 min in Settings: the recorder free, its card's minutes come later
  local a b
  a=$(ms); ui BluetoothOff || { verdict away FAIL "Bluetooth not turned off (ui log)"; ui BluetoothOn; return; }
  [ -n "$FAKE" ] || { free 60 && log "$NAME free: recording to its card" || log "$NAME not seen free"; }
  sleep 120
  ui BluetoothOn || { verdict away FAIL "Bluetooth not turned on again (ui log): turn it on by hand"; return; }
  b=$(ms)
  ev wait-audio "$NAME" "$(( a - 10000 ))" "$(( b + 30000 ))" 600 && verdict away PASS "2 min with Bluetooth off came from the card, gap-free" \
    || verdict away FAIL "the time away is missing (above)"
}

step_background() {  # 10 min in the background (LOCK=1: locked): 20 s pieces, gap-free
  local a; a=$(ms)
  if [ -n "${LOCK:-}" ]; then ui Lock; else ui Home; fi
  sleep 600
  ev wait-audio "$NAME" "$(( a + 30000 ))" "$(( a + 580000 ))" 300 \
    && verdict background PASS "10 min $([ -n "${LOCK:-}" ] && echo locked || echo "in the background (home; locked: LOCK=1)"), gap-free" \
    || verdict background FAIL "gaps in the background (above)"
}

step_quit() {  # swiped away, a minute later opened: it connects by itself and the minute comes from the card
  local a b q
  a=$(ms); ui Quit
  sleep 20; q=$(pid); [ -n "$q" ] && log "the app runs again 20 s after the quit (pid $q): iOS relaunched it"
  [ -n "$FAKE" ] || { free 30 && log "$NAME free after the quit" || log "$NAME not seen free after the quit"; }
  sleep 40
  ui Open LR_WAIT_S=120 || { verdict quit FAIL "not following live after opening (ui log)"; return; }
  b=$(ms)
  ev wait-audio "$NAME" "$a" "$(( b - 5000 ))" 600 && verdict quit PASS "reopened: connected by itself, the minute away from the card" \
    || verdict quit FAIL "audio missing after reopening (above)"
}

step_kill() {  # the nearest to iOS ending it: the process kills itself (SIGKILL) in the background; iOS should relaunch it
  local a p q= k
  ui Launch LR_E2E_KILL=120 || { verdict kill FAIL "no launch"; return; }
  p=$(pid); a=$(ms)
  ui Home
  log "the app (pid $p) kills itself 2 min after its launch, in the background"
  k=$SECONDS
  while [ $((SECONDS - k)) -lt 300 ]; do sleep 5; q=$(pid); [ -n "$q" ] && [ "$q" != "$p" ] && break; q=; done
  if [ -n "$q" ]; then log "after the kill: relaunched by iOS (pid $q)"; else log "after the kill: not relaunched within 5 min"; fi
  sleep 60
  if [ -n "$q" ] && ev wait-audio "$NAME" "$a" "$(( $(ms) - 20000 ))" 300; then verdict kill PASS "relaunched in the background (pid $q), gap-free"
  else verdict kill FAIL "relaunch ${q:-none}; audio above"; ui Open LR_WAIT_S=120 >/dev/null; fi
}

step_kicked() {  # another phone (this Mac) force-binds it: the app says it was kicked and stays away
  real kicked || return 0
  local t
  ui Launch LR_E2E="$(settings "$IDLE")"
  free 60 || { verdict kicked FAIL "$NAME still held by the phone"; return; }
  log "OWNER: if this Mac asks to pair with $NAME, click Pair"
  ble forget $NAME || { verdict kicked FAIL "the Mac could not pair and FORGET (a stale bond on the Mac? forget $NAME there)"; return; }
  ble enc $NAME >/dev/null || { verdict kicked FAIL "the Mac holds no bond after FORGET"; return; }
  t=$(ms)
  ui Launch LR_E2E="$(settings "$LINK")"
  ev wait-event kicked "$t" 180 && verdict kicked PASS "kicked event; the app stopped reconnecting" || verdict kicked FAIL "no kicked event within 3 min"
  ui Kicked LR_WAIT_S=30 && verdict kicked-page PASS "the page says 已被其他手机绑定" || verdict kicked-page FAIL "the page does not say so (ui log)"
}

step_readd() {  # taking it back: the app's advice to forget it, done in Settings, paired again; the Mac is out
  real readd || return 0
  local t; t=$(ms); local from; from=$(wc -l < "$R/run.log")
  ui Add LR_WAIT_S=300 || { verdict readd FAIL "not following live again (ui log)"; return; }
  tail -n +"$from" "$R/run.log" | grep -q "^LRUI advice shown" && verdict readd-advice PASS "the app said to forget it in Settings; done there, paired again" \
    || verdict readd-advice FAIL "no 忽略此设备 advice: iOS paired without forgetting?"
  ev wait-status "$NAME" "$t" 120 && verdict readd PASS "bound again" || verdict readd FAIL "no status report"
  ble enc $NAME >/dev/null
  case $? in
    0) verdict readd-mac FAIL "the Mac can still read encrypted: $NAME did not forget it" ;;
    1) verdict readd-mac PASS "the Mac is forgotten (forget $NAME in the Mac's Bluetooth settings afterwards)" ;;
    *) verdict readd-mac FAIL "$NAME not found by the Mac" ;;
  esac
}

step_unbind() {  # 解除绑定: it forgets every phone and stops recording; then Settings › Bluetooth › 忽略此设备
  local t; t=$(ms)
  ui Unbind || { verdict unbind FAIL "the page did not unbind or Settings did not forget (ui log)"; return; }
  ev wait-event unbind "$t" 120 || { verdict unbind FAIL "no unbind event"; return; }
  if real unbind-state; then
    sleep 30
    local st; st=$(state)
    [ "$st" = 0 ] && verdict unbind PASS "$NAME advertises 0x00 (unbound, free); forgotten in Settings" || verdict unbind FAIL "$NAME state $st (0 expected)"
  fi
  sleep 60
  ev none "$NAME" "$(( $(ms) - 60000 ))" "$(ms)" && verdict unbind-audio PASS "nothing recorded since" || verdict unbind-audio FAIL "audio after unbinding"
}

step_finish() {  # the test app idle and unbound; server, tunnel, watch, fake stopped; the table
  ui Launch LR_E2E="$(settings "${IDLE%\}}, \"bleName\": null, \"bleId\": null, \"bleKicked\": null}")" && ui Quit
  if [ -s "$W/autolock" ]; then
    ui AutoLock LR_AUTOLOCK="$(cat "$W/autolock")" && { log "Auto-Lock back to $(cat "$W/autolock")"; rm "$W/autolock"; } \
      || log "OWNER: set Auto-Lock back to $(cat "$W/autolock") (Settings › 显示与亮度 › 自动锁定)"
  fi
  step_stop
  log "stopped; results in $R"
  column -t -s $'\t' "$R/summary.tsv" | tee -a "$R/run.log"
  [ -n "$FAKE" ] || log "OWNER: forget $NAME in this Mac's Bluetooth settings"
}

step_stop() {  # server, tunnel, adverts watch, fake (also on Ctrl-C)
  for p in server tunnel fake; do [ -f "$W/$p.pid" ] && kill "$(cat "$W/$p.pid")" 2>/dev/null; rm -f "$W/$p.pid"; done
  pkill -f "$E/ble.py watch" 2>/dev/null
  return 0
}
trap 'log "interrupted: helpers stopped; the test app may still be bound and linked: uitest.sh finish"; step_stop; exit 130' INT TERM

for s in $STEPS; do
  type "step_$s" >/dev/null 2>&1 || { echo "no step $s"; exit 2; }
  log "-- $s"; "step_$s"
done
