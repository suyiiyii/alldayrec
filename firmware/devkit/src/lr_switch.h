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
