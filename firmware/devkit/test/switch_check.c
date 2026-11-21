// Host check of LR-P3's switch debounce and LR-N5-S's key (src/lr_switch.h):
// cc -o switch_check test/switch_check.c && ./switch_check
#include <assert.h>
#include <stdio.h>
#define CONFIG_LR_SWITCH_KEY_LONG_MS 2000
#include "../src/lr_switch.h"

// the key down or up for ms, read every LR_SWITCH_POLL_MS -> the events: short presses + 100 * long ones
static int key(struct lr_key *k, bool down, int ms)
{
    int e = 0;
    for (int t = 0; t < ms; t += LR_SWITCH_POLL_MS) {
        int r = lr_key_step(k, down);
        e += r == LR_KEY_SHORT ? 1 : r == LR_KEY_LONG ? 100 : 0;
    }
    return e;
}

static void key_check(void)
{
    struct lr_key k = {0};
    assert(key(&k, false, 100) == 0);
    assert(key(&k, true, 300) == 0 && key(&k, false, 100) == 1);  // a short press: SHORT as it is let go
    assert(key(&k, true, 2000) == 0 && key(&k, true, 50) == 100);  // held: LONG 2 s after the debounce, still down
    assert(key(&k, true, 10000) == 0 && key(&k, false, 100) == 0);  // once: nothing more held, nothing as let go
    assert(key(&k, true, 50) == 0 && key(&k, false, 100) == 0);  // one read down (a bounce, 50 ms): no press
    for (int i = 0; i < 100; i++) {  // contacts bouncing every read for 5 s: never a level, no press
        assert(key(&k, i & 1, 50) == 0);
    }
    assert(key(&k, false, 100) == 0 && key(&k, true, 2100) == 100);  // after the bouncing, a long press works

    struct lr_key boot = {0};  // held at power-up (or stuck in the case): nothing, ever, while held
    assert(key(&boot, true, 60000) == 0);
    assert(key(&boot, false, 100) == 0);  // let go: no SHORT for it either
    assert(key(&boot, true, 2100) == 100);  // the next press counts
    struct lr_key boot2 = {0};  // held at power-up for less than a long press: let go, no SHORT
    assert(key(&boot2, true, 500) == 0 && key(&boot2, false, 100) == 0);

    struct lr_key woken = LR_KEY_WOKEN;  // woken by this press: let go, nothing (the wake has told the state)
    assert(key(&woken, false, 100) == 0);
    assert(key(&woken, true, 300) == 0 && key(&woken, false, 100) == 1);  // the next short press is one
    struct lr_key held = LR_KEY_WOKEN;  // held on from the wake: LONG at 2 s of reads, once
    assert(key(&held, true, 2000) == 100 && key(&held, true, 30000) == 0 && key(&held, false, 100) == 0);
}

// lr_key_wake's reads: the key down for the first `down`, and for 4 (200 ms) from `tap`; USB from `usb` on
static int reads, beeps, down, tap, usb;
static int script(void)
{
    int i = reads++;
    return i >= usb ? -1 : i < down || (i >= tap && i < tap + 4);
}
static void count(int n)
{
    beeps += n;
}
static int wake(int down_reads, int tap_at, int usb_at)
{
    struct lr_key k;
    reads = beeps = 0, down = down_reads, tap = tap_at, usb = usb_at;
    return lr_key_wake(&k, script, count);
}

static void wake_check(void)
{
    int all = LR_KEY_WAKE_MS / LR_SWITCH_POLL_MS, never = 1 << 20;
    // a tap wakes it (let go before the first read): 3 beeps in all, the 5 s from the wake on, then down again
    assert(wake(0, never, never) == LR_KEY_NONE && beeps == 3 && reads == all);
    // the wake's press let go only 0.6 s in: no SHORT, no more beeps, the 5 s not restarted
    assert(wake(12, never, never) == LR_KEY_NONE && beeps == 3 && reads == all);
    // held on from the wake: LONG at 2 s of reads, 3 + 2 beeps, recording
    assert(wake(never, never, never) == LR_KEY_LONG && beeps == 5 && reads == 40);
    // a second tap at 4 s: 3 more beeps, the 5 s still from the wake
    assert(wake(0, 80, never) == LR_KEY_NONE && beeps == 6 && reads == all);
    // USB 1 s in: up on USB
    assert(wake(0, never, 20) == -1 && beeps == 3);
}

// reads of one position every LR_SWITCH_POLL_MS for ms -> what the last read says
static int hold(bool on, int ms, bool *last, int *n)
{
    int r = -1;
    for (int t = 0; t < ms; t += LR_SWITCH_POLL_MS) {
        r = lr_switch_debounce(on, last, n);
    }
    return r;
}

int main(void)
{
    bool last = false;
    int n = 0;
    assert(hold(true, 500, &last, &n) == -1);  // reads over 0.45 s: not yet
    assert(hold(true, 50, &last, &n) == 1);    // the one 0.5 s after the first: on
    for (int i = 0; i < 40; i++) {             // a lever bouncing every 50 ms for 2 s: never settles
        assert(hold(i & 1, 50, &last, &n) == -1);
    }
    assert(hold(false, 550, &last, &n) == 0);  // then off for 0.5 s: off
    assert(hold(true, 450, &last, &n) == -1 && hold(false, 50, &last, &n) == -1);  // on for less: still not on
    assert(hold(true, 550, &last, &n) == 1);
    assert(hold(false, 450, &last, &n) == -1 && hold(true, 600, &last, &n) == 1);  // running: off for less, back on
    key_check();
    wake_check();
    puts("switch_check ok");
    return 0;
}
