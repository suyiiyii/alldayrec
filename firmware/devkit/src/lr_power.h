#ifndef LR_POWER_H
#define LR_POWER_H

#include <stdbool.h>
#include <stdint.h>

/* What a POWER event is (its a, lr_log.h) */
enum { LR_POWER_MINUTE, LR_POWER_WARNING, LR_POWER_LOW };

/* After lr_log_init, before the card and the radio: the restart after a low battery goes to System OFF here; else
 * the battery is read once a minute from now on (lr_power.c) */
void lr_power_init(void);
/* The open file written out and closed, the phone told: before a restart that stops recording */
void lr_power_stop(void);
/* The log to flash, then System OFF with the card's and the mic's supplies off; USB (VBUS) wakes it, and LR-P3's
 * switch moving. CONFIG_LR_PARK: parked in System ON instead, the same wakes as a restart (lr_power.c). Does not
 * return. */
void lr_power_off(void);

/* What the board runs on, for the status read (lr_store.c, byte 47): the phone shows it next to the battery */
enum { LR_ON_BATTERY, LR_CHARGING, LR_CHARGED };
uint8_t lr_charge(void);

/* usb: VBUS there; chg: the charger's status pin (battery_charging: 1 charging, 0 done, negative: the board cannot read
 * it, the ProMicro: on USB it says charging) */
static inline uint8_t lr_charge_state(bool usb, int chg)
{
    return !usb ? LR_ON_BATTERY : chg == 0 ? LR_CHARGED : LR_CHARGING;
}

/* A minute's battery reading: how many in a row are under limit now. On USB (the reading is the cable's), a failed
 * read, or one the board could not run at (a misread, not an empty battery): none. */
static inline int lr_low_count(int count, bool usb, int err, uint16_t mv, uint16_t limit)
{
    return !usb && !err && mv > 2000 && mv < limit ? count + 1 : 0;
}

/* A second of the park (CONFIG_LR_PARK), what restarts it: the switch moved, USB; after a low battery (low) also the
 * charger charging, or the battery read (err 0) back at LR_PARK_UP_MV. Switched off, charging is no reason: on a board
 * that cannot see VBUS, every boot would find the switch off and park again. */
#define LR_PARK_UP_MV 3600  // a cell rested after the 3.3 V cut reads ~3.35-3.45 V: this is charging
static inline bool lr_park_wake(bool moved, bool usb, bool low, bool charging, int err, uint16_t mv)
{
    return moved || usb || (low && (charging || (!err && mv >= LR_PARK_UP_MV)));
}

#endif
