/* LR-P3's slide switch, CONFIG_LR_SWITCH: P1.15 on its pull-up, closed to GND = on (LR-N4: open = on,
 * CONFIG_LR_SWITCH_OPEN_ON). A position counts once it has read the same for 0.5 s (lr_switch_debounce): 116C's lever
 * bounced on and off for seconds on 10-07, four boots in a row, two of them mounting the card. Off ends the open file,
 * drops the phone and restarts; a boot that finds the switch off, or not on for 0.5 s within 3 s, goes to System OFF at
 * once (CONFIG_LR_PARK: the park, lr_power.c), before the card or the radio, its BOOT the only record, and wakes when
 * the switch moves or USB comes (VBUS). Switched off on USB it stays up instead, not recording (the USB port still
 * takes a firmware), until the switch is on (restart, recording) or the cable goes (System OFF or the park).
 * Switched on it records with or without VBUS: only the switch is watched, and on USB lr_power.c skips the low-battery
 * cut; a wireless pad's VBUS coming and going does not stop it (VBUS_GONE matters only switched off).
 * Events: SWITCH (lr_log.h); the wake reason is BOOT's RESETREAS (OFF: the switch, VBUS: USB), parked the POWER a 3
 * before it (its RESETREAS SREQ).
 *
 * LR-N5-S (CONFIG_LR_SWITCH_KEY): SW1 on P0.31 (sw-gpios) is a momentary key, low = pressed (lr_key_step). A long
 * press toggles recording and stopped; a short one tells the state. Stopped on battery is System OFF, not the park:
 * a press of any length wakes it in hardware (SENSE), where the park would read the key once a second; only under
 * CONFIG_LR_SWITCH_KEY_OFF_MV (3.6 V) it parks, as System OFF at a low cell may never come back (lr_power.c). Woken
 * by the key it tells "stopped" once and waits 5 s from the wake for a long press (lr_key_wake), then goes down again,
 * once the key is up. Beeps (each one buzzer_segment, <= 20 ms; silent while CONFIG_LR_BUZZER_DUTY_MAX is 0) with as
 * many flashes of LED_G: 1 short press while recording, 2 recording, 3 stopped.
 *   recording:          short 1 | long 3, stopped | USB in/out: nothing changes
 *   stopped, battery:   System OFF (>= 3.6 V) or the park; a press wakes it (the park: held over its 1 s read): 3,
 *                       then 5 s for a long press (2, recording), else down again once the key is up | USB in: wakes
 *                       it, stopped on USB
 *   woken, the 5 s:     short 3, the 5 s go on | long 2, recording
 *   stopped, USB:       short 3 | long 2, recording | USB out: System OFF or the park, as on battery
 *   any other boot:     recording (a battery put in, a brown-out, a firmware, a watchdog); a key held then counts
 *                       once let go
 *   low battery:        the park as before (lr_power.c): a key held over a 1 s read, or USB, restarts it */
#include "lr_switch.h"

#include <hal/nrf_gpio.h>
#include <hal/nrf_power.h>
#include <zephyr/kernel.h>

#include "config.h"
#include "led.h"
#include "lr_log.h"
#include "lr_power.h"
#include "wdog_facade.h"
#ifdef CONFIG_LR_SWITCH_KEY
#include "buzzer.h"
#include "lib/battery/battery.h"
#endif

#if DT_NODE_HAS_PROP(LR_USER, sw_gpios)  // LR-N5-S's key (lr_n5s.overlay)
#define SWITCH LR_PIN(sw_gpios)
#else
#define SWITCH NRF_GPIO_PIN_MAP(1, 15)  // the other ProMicros leave it open: it would read off, hence the Kconfig
#endif
#define BOOT_MS 3000                    // a boot waits this long at most for the switch to settle
#define VBUS_GONE 6                     // polls in a row without VBUS (0.3 s): the cable is out

#ifndef CONFIG_LR_SWITCH_KEY
static bool last;  // lr_switch_debounce's state, from boot on
static int n;
#endif
extern bool is_off;  // main.c's status flashes stop while it goes down

static bool on(void)
{
    return nrf_gpio_pin_read(SWITCH) == IS_ENABLED(CONFIG_LR_SWITCH_OPEN_ON);
}

static bool vbus(void)
{
    return nrf_power_usbregstatus_vbusdet_get(NRF_POWER);
}

static void restart(bool now_on, uint32_t then)
{
    lr_log(LR_SWITCH, now_on, then, 0);
    lr_log_sync();
    NVIC_SystemReset();
}

void lr_switch_arm(void)
{
    nrf_gpio_cfg_input(SWITCH, NRF_GPIO_PIN_PULLUP);
    k_msleep(1);
#ifdef CONFIG_LR_SWITCH_KEY
    // a press only: one come since off() saw the key up wakes it at once and off() waits for it again, where SENSE_HIGH
    // would hold a stuck key's 0.23 mA through the pull-up all through System OFF
    nrf_gpio_cfg_sense_input(SWITCH, NRF_GPIO_PIN_PULLUP, NRF_GPIO_PIN_SENSE_LOW);
#else
    // wakes on the other level, whichever way round on is
    nrf_gpio_cfg_sense_input(SWITCH, NRF_GPIO_PIN_PULLUP,
                             nrf_gpio_pin_read(SWITCH) ? NRF_GPIO_PIN_SENSE_LOW : NRF_GPIO_PIN_SENSE_HIGH);
#endif
}

/* The park (lr_power.c, CONFIG_LR_PARK): the pin's level, its pull-up on for this read only (on, the switch closed to
 * GND would draw ~0.23 mA through it all along, as it does in System OFF) */
bool lr_switch_read(void)
{
    nrf_gpio_cfg_input(SWITCH, NRF_GPIO_PIN_PULLUP);
    k_busy_wait(10);
    bool level = nrf_gpio_pin_read(SWITCH);
    nrf_gpio_cfg_default(SWITCH);
    return level;
}

#ifdef CONFIG_LR_SWITCH_KEY
#define KEY_OFF 0x5703B0FF
#define KEY_PARKED 0x5703BA4C
// KEY_OFF: this restart is a long press's stop; KEY_PARKED: the park's (off() under CONFIG_LR_SWITCH_KEY_OFF_MV), a
// press or USB. A cold start's RAM fails both
static __noinit uint32_t key_off;
static struct lr_key key;

/* n beeps (buzzer_segment, <= 20 ms each; silent while CONFIG_LR_BUZZER_DUTY_MAX is 0), each with a flash of led2
 * (R1: LED_G): 1 a short press while recording, 2 recording from now on, 3 stopped */
static void hint(int n)
{
    for (int i = 0; i < n; i++) {
        set_led_blue(true);
        buzzer_segment();
        k_msleep(40);
        set_led_blue(false);
        k_msleep(150);
    }
}

/* Stopped on battery: System OFF once the key has been up for two reads (read with its pull-up on for 10 us only, so a
 * key stuck down costs no 0.23 mA through it), then SENSE armed for the next press: never down, or it would wake at
 * once, again and again. The battery under CONFIG_LR_SWITCH_KEY_OFF_MV (or not read): the park instead (lr_power.c:
 * one LR-N4 sent to System OFF at 3.28 V never came back), a press held over its 1 s read wakes it. USB coming
 * meanwhile: up on USB instead. SWITCH's b: the battery, mV. */
static void off(void)
{
    uint16_t mv = 0;
    bool park = battery_get_millivolt(&mv) || mv < CONFIG_LR_SWITCH_KEY_OFF_MV;
    lr_log(LR_SWITCH, 0, LR_SWITCH_SYSTEM_OFF, mv);
    lr_log_sync();  // the flash write before the key is seen up: from then till SENSE is armed nothing slow
    for (int up = 0; up < LR_KEY_READS;) {
        k_msleep(LR_SWITCH_POLL_MS);
        watchdog_feed();
        if (vbus()) {
            key_off = KEY_OFF;
            NVIC_SystemReset();
        }
        up = lr_switch_read() ? up + 1 : 0;
    }
    NRF_POWER->RESETREAS = NRF_POWER->RESETREAS;  // the next boot's OFF / VBUS are that wake's (BOOT has logged these)
    key_off = park ? KEY_PARKED : 0;
    lr_power_off(park);
}

/* Recording: SHORT 1 beep, LONG 3 and a restart into stopped (System OFF, or up on USB) */
static void poll(struct k_work *w)
{
    int e = lr_key_step(&key, on());
    if (e == LR_KEY_SHORT) {
        hint(1);
    }
    if (e != LR_KEY_LONG) {
        k_work_reschedule(k_work_delayable_from_work(w), K_MSEC(LR_SWITCH_POLL_MS));
        return;
    }
    is_off = true;
    hint(3);
    lr_power_stop();
    key_off = KEY_OFF;
    restart(false, LR_SWITCH_RESTART);
}
static K_WORK_DELAYABLE_DEFINE(poll_work, poll);

/* The wake's 5 s (lr_key_wake): a read each LR_SWITCH_POLL_MS, -1 once USB is there */
static int wake_read(void)
{
    k_msleep(LR_SWITCH_POLL_MS);
    watchdog_feed();
    return vbus() ? -1 : on();
}

static bool record(void)
{
    lr_log(LR_SWITCH, 1, LR_SWITCH_RECORDING, 0);
    k_work_schedule(&poll_work, K_MSEC(LR_SWITCH_POLL_MS));
    return true;
}

/* A long press's restart, or a wake by USB (System OFF and the park are only ever stopped): stopped. Woken by the key:
 * "stopped", then 5 s for a long press (lr_key_wake). Any other boot (a battery put in, a brown-out, a firmware's
 * restart, a watchdog): recording, the key not counted till it has been up. */
bool lr_switch_boot(void)
{
    nrf_gpio_cfg_input(SWITCH, NRF_GPIO_PIN_PULLUP);
    k_msleep(1);
    uint32_t reas = NRF_POWER->RESETREAS;
    bool stop = key_off == KEY_OFF, parked = key_off == KEY_PARKED;
    key_off = 0;
    if (stop || reas & POWER_RESETREAS_VBUS_Msk || (parked && vbus())) {
        NRF_POWER->RESETREAS = reas;  // not recording: lr_store never clears it, and a stale VBUS would stop the next
        if (!vbus()) {
            off();
        }
        lr_log(LR_SWITCH, 0, LR_SWITCH_PARKED, 0);
        return false;
    }
    // RESETREAS adds up till cleared: OFF with a reset since (a watchdog after a key wake) is that reset's boot
    uint32_t resets = POWER_RESETREAS_RESETPIN_Msk | POWER_RESETREAS_DOG_Msk | POWER_RESETREAS_SREQ_Msk |
                      POWER_RESETREAS_LOCKUP_Msk;
    if (!parked && (reas & (POWER_RESETREAS_OFF_Msk | resets)) != POWER_RESETREAS_OFF_Msk) {
        return record();
    }
    int e = lr_key_wake(&key, wake_read, hint);
    if (e == LR_KEY_LONG) {
        return record();
    }
    if (e < 0) {
        NRF_POWER->RESETREAS = reas;
        lr_log(LR_SWITCH, 0, LR_SWITCH_PARKED, 0);
        return false;
    }
    off();
    return false;  // not reached
}

/* Stopped on USB: SHORT 3 beeps, LONG 2 and a restart into recording; the cable out: System OFF */
void lr_switch_park(void)
{
    int gone = 0;
    for (;;) {
        k_msleep(LR_SWITCH_POLL_MS);
        watchdog_feed();
        gone = vbus() ? 0 : gone + 1;
        int e = lr_key_step(&key, on());
        if (e == LR_KEY_SHORT) {
            hint(3);
        }
        if (e == LR_KEY_LONG) {
            hint(2);
            restart(true, LR_SWITCH_RESTART);
        }
        if (gone >= VBUS_GONE) {
            off();
        }
    }
}
#else
static void power_off(void)
{
    lr_log(LR_SWITCH, 0, LR_SWITCH_SYSTEM_OFF, 0);
    lr_power_off(true);
}

/* Running: off for 0.5 s, the open file closed and the phone told before the restart that decides what comes next */
static void poll(struct k_work *w)
{
    if (lr_switch_debounce(on(), &last, &n) != 0) {
        k_work_reschedule(k_work_delayable_from_work(w), K_MSEC(LR_SWITCH_POLL_MS));
        return;
    }
    is_off = true;
    k_msleep(500);  // a status flash under way ends first
    for (int i = 0; i < 3; i++) {  // switched off: 3 quick flashes (led2: P0.15), then down
        set_led_blue(true);
        k_msleep(100);
        set_led_blue(false);
        k_msleep(100);
    }
    lr_power_stop();
    restart(false, LR_SWITCH_RESTART);
}
static K_WORK_DELAYABLE_DEFINE(poll_work, poll);

bool lr_switch_boot(void)
{
    nrf_gpio_cfg_input(SWITCH, NRF_GPIO_PIN_PULLUP);
    k_msleep(1);
    int at = lr_switch_debounce(on(), &last, &n);
    for (int t = 0; at < 0 && t < BOOT_MS; t += LR_SWITCH_POLL_MS) {
        k_msleep(LR_SWITCH_POLL_MS);
        at = lr_switch_debounce(on(), &last, &n);
    }
    if (at == 1) {
        lr_log(LR_SWITCH, 1, LR_SWITCH_RECORDING, 0);
        k_work_schedule(&poll_work, K_MSEC(LR_SWITCH_POLL_MS));
        return true;
    }
    if (!vbus()) {
        lr_power_off(true);  // no SWITCH event: a lever bouncing in System OFF wakes it again and again, one BOOT each
    }
    lr_log(LR_SWITCH, 0, LR_SWITCH_PARKED, 0);
    return false;
}

void lr_switch_park(void)
{
    int gone = 0;
    for (;;) {
        k_msleep(LR_SWITCH_POLL_MS);
        watchdog_feed();
        gone = vbus() ? 0 : gone + 1;
        if (lr_switch_debounce(on(), &last, &n) == 1) {
            restart(true, LR_SWITCH_RESTART);
        }
        if (gone >= VBUS_GONE) {
            power_off();
        }
    }
}
#endif
