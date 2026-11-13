// Host check of lr_low_count, lr_park_wake and lr_park_end (src/lr_power.h), what turns a recorder off and on again: cc -o power_check test/power_check.c && ./power_check
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
    // a second of the park (lr_park_wake): what restarts it, as the bits the park's end logs
    assert(lr_park_wake(false, false, true, false, -EAGAIN, 0) == 0);  // low, nothing new: parked on
    assert(lr_park_wake(true, false, false, false, -EAGAIN, 0) == LR_WAKE_SWITCH);  // the switch moved
    assert(lr_park_wake(false, true, false, false, -EAGAIN, 0) == LR_WAKE_USB);
    assert(lr_park_wake(false, false, true, true, -EAGAIN, 0) == LR_WAKE_CHARGING);  // USB the board cannot see
    assert(lr_park_wake(false, false, false, true, 0, 4000) == 0);  // switched off: charging or a full cell, parked on
    assert(lr_park_wake(false, false, true, false, 0, 3600) == LR_WAKE_BATTERY);  // low, the battery back at 3.6 V
    assert(lr_park_wake(false, false, true, false, 0, 3590) == 0);
    assert(lr_park_wake(false, false, true, false, -EIO, 3700) == 0);  // a failed read
    assert(lr_park_wake(true, true, true, true, 0, 3700) == 15);       // all at once: every bit
    assert(lr_park_wake(false, true, true, true, -EAGAIN, 0) == (LR_WAKE_USB | LR_WAKE_CHARGING));
    // the park's end (POWER a 3's b): seconds in bits 0-27, the wake in 28-31, as recorderLog.check.ts decodes it
    assert(lr_park_end(1, LR_WAKE_SWITCH) == 0x10000001u);
    assert(lr_park_end(3600, LR_WAKE_BATTERY) == 0x80000E10u);
    assert(lr_park_end(LR_PARK_S_MAX, 15) == 0xFFFFFFFFu);
    assert(lr_park_end(0xFFFFFFFFu, LR_WAKE_USB) == (0x20000000u | LR_PARK_S_MAX));  // saturates, the wake intact
    puts("power_check ok");
    return 0;
}
