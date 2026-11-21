#ifndef LR_SWITCH_H
#define LR_SWITCH_H

#include <stdbool.h>

/* What follows a SWITCH event (its a) */
enum { LR_SWITCH_RECORDING, LR_SWITCH_SYSTEM_OFF, LR_SWITCH_PARKED, LR_SWITCH_RESTART };

#define LR_SWITCH_POLL_MS 50
#define LR_SWITCH_STABLE_MS 500  // a position counts once read this long: a knock, a loose lever bounces

/* One read of the switch (on), every LR_SWITCH_POLL_MS; *last and *n (reads in a row that agree) carry over from read
 * to read. 1: on for LR_SWITCH_STABLE_MS, 0: off for as long, -1: not settled. */
static inline int lr_switch_debounce(bool on, bool *last, int *n)
{
    *n = on == *last ? *n + 1 : 1;
    *last = on;
    return (*n - 1) * LR_SWITCH_POLL_MS >= LR_SWITCH_STABLE_MS ? on : -1;
}

#ifdef CONFIG_LR_SWITCH_KEY_LONG_MS
/* LR-N5-S's momentary key (CONFIG_LR_SWITCH_KEY), read every LR_SWITCH_POLL_MS: a level counts once LR_KEY_READS reads
 * agree (contact bounce is a few ms). A press counts only once the key has been seen up since boot: one held at
 * power-up, or stuck in the case, does nothing until it is let go. LONG comes once, while held, at
 * CONFIG_LR_SWITCH_KEY_LONG_MS; SHORT on letting go of a shorter one. */
enum { LR_KEY_NONE, LR_KEY_SHORT, LR_KEY_LONG };
#define LR_KEY_READS 2
struct lr_key {
    bool raw;    // the last read
    int n;       // reads in a row that agree with it
    bool down;   // debounced
    bool armed;  // seen up since boot
    int held;    // ms down, stops at the long press
    bool woke;   // this press woke it: held on it is a long press, let go no SHORT (the wake has told the state)
};
/* Woken by the key (from System OFF or the park): that press is down and counts as a long one only */
#define LR_KEY_WOKEN {.raw = true, .n = LR_KEY_READS, .down = true, .armed = true, .woke = true}

static inline int lr_key_step(struct lr_key *k, bool pressed)
{
    k->n = pressed == k->raw ? k->n + 1 : 1;
    k->raw = pressed;
    if (k->n < LR_KEY_READS) {
        return LR_KEY_NONE;
    }
    if (!pressed) {
        int e = k->down && k->armed && !k->woke && k->held < CONFIG_LR_SWITCH_KEY_LONG_MS ? LR_KEY_SHORT : LR_KEY_NONE;
        k->down = false;
        k->armed = true;
        k->woke = false;
        k->held = 0;
        return e;
    }
    k->down = true;
    if (k->held >= CONFIG_LR_SWITCH_KEY_LONG_MS) {
        return LR_KEY_NONE;
    }
    k->held += LR_SWITCH_POLL_MS;
    return k->armed && k->held >= CONFIG_LR_SWITCH_KEY_LONG_MS ? LR_KEY_LONG : LR_KEY_NONE;
}

#define LR_KEY_WAKE_MS 5000  // woken by the key: this long for a long press
/* Woken by the key: hint(3) ("stopped"), then LR_KEY_WAKE_MS from the wake on, never restarted, for a long press:
 * hint(2), LR_KEY_LONG (recording). read() waits LR_SWITCH_POLL_MS, then 1: the key down, 0: up, < 0: USB came (-1).
 * The wake's own press let go is nothing; a later short press hint(3)s again. LR_KEY_NONE: the 5 s are over. */
static inline int lr_key_wake(struct lr_key *k, int (*read)(void), void (*hint)(int))
{
    *k = (struct lr_key)LR_KEY_WOKEN;
    hint(3);
    for (int t = 0; t < LR_KEY_WAKE_MS; t += LR_SWITCH_POLL_MS) {
        int r = read();
        if (r < 0) {
            return -1;
        }
        int e = lr_key_step(k, r);
        if (e == LR_KEY_LONG) {
            hint(2);
            return LR_KEY_LONG;
        }
        if (e == LR_KEY_SHORT) {
            hint(3);
        }
    }
    return LR_KEY_NONE;
}
#endif

/* At boot, after lr_log_init: true if on for 0.5 s (recording goes ahead, the switch watched from now on); off, or not
 * settled within 3 s, without USB it does not return (System OFF); on USB, false: start USB, then lr_switch_park(). */
bool lr_switch_boot(void);
/* Up on USB, not recording, until the switch is on (restart) or the cable goes (System OFF). Does not return. */
void lr_switch_park(void);
/* Before System OFF (lr_power_off): the switch moving either way wakes it */
void lr_switch_arm(void);
/* Parked instead of System OFF (CONFIG_LR_PARK): the pin read once, its pull-up on only meanwhile */
bool lr_switch_read(void);

#endif
