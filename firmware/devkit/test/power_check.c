// Host check of lr_low_count and lr_park_wake (src/lr_power.h), what turns a recorder off and on again: cc -o power_check test/power_check.c && ./power_check
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include "../src/lr_power.h"

// readings a minute apart -> the minute the third low one in a row comes (1-based), 0: never
static int off_at(const uint16_t *mv, int n, bool usb, int err, uint16_t limit)
{
    for (int i = 0, low = 0; i < n; i++) {
        if ((low = lr_low_count(low, usb, err, mv[i], limit)) >= 3) {
            return i + 1;
        }
    }
    return 0;
}

int main(void)
{
    const uint16_t fall[] = {3400, 3320, 3290, 3280, 3260, 3240}, blip[] = {3290, 3290, 3310, 3290, 3290, 3310};
    const uint16_t misread[] = {0, 0, 0, 1500, 1500, 1500};
    assert(off_at(fall, 6, false, 0, 3300) == 5);   // three in a row under 3.3 V
    assert(off_at(blip, 6, false, 0, 3300) == 0);   // one over starts the count again
    assert(off_at(fall, 6, true, 0, 3300) == 0);    // on USB: the cable's voltage
    assert(off_at(fall, 6, false, -5, 3300) == 0);  // the ADC failed
    assert(off_at(misread, 6, false, 0, 3300) == 0);  // no board runs at that: a misread
    assert(off_at(fall, 6, false, 0, 0) == 0);      // CONFIG_LR_LOW_BATTERY_MV 0: never
    // what the status read says the board runs on (lr_charge_state)
    assert(lr_charge_state(false, 0) == LR_ON_BATTERY);  // unplugged: ~CHG is open, as when done
    assert(lr_charge_state(false, -ENOTSUP) == LR_ON_BATTERY);
    assert(lr_charge_state(true, 1) == LR_CHARGING);
    assert(lr_charge_state(true, 0) == LR_CHARGED);  // the X1's charger done
    assert(lr_charge_state(true, -ENOTSUP) == LR_CHARGING);  // the ProMicro: USB is all it knows
    // a second of the park (lr_park_wake): what restarts it
    assert(!lr_park_wake(false, false, true, false, -EAGAIN, 0));  // low, nothing new: parked on
    assert(lr_park_wake(true, false, false, false, -EAGAIN, 0));   // the switch moved
    assert(lr_park_wake(false, true, false, false, -EAGAIN, 0));   // USB
    assert(lr_park_wake(false, false, true, true, -EAGAIN, 0));    // low and charging: USB the board cannot see
    assert(!lr_park_wake(false, false, false, true, 0, 4000));     // switched off: charging or a full cell, parked on
    assert(lr_park_wake(false, false, true, false, 0, 3600));      // low, the battery back at 3.6 V
    assert(!lr_park_wake(false, false, true, false, 0, 3590));
    assert(!lr_park_wake(false, false, true, false, -EIO, 3700));  // a failed read
    puts("power_check ok");
    return 0;
}
