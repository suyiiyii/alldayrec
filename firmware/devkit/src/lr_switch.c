/* LR-P3's slide switch, CONFIG_LR_SWITCH: P1.15 on its pull-up, closed to GND = on. A position counts once
 * it has read the same for 0.5 s (lr_switch_debounce): 116C's lever bounced on and off for seconds on 10-07, four
 * boots in a row, two of them mounting the card. Off ends the open file, drops the phone and restarts; a boot that
 * finds the switch off, or not on for 0.5 s within 3 s, goes to System OFF at once (CONFIG_LR_PARK: the park,
 * lr_power.c), before the card or the radio, its BOOT the only record, and wakes when the switch moves or USB comes
 * (VBUS). On USB it stays up instead, not recording (the USB port still takes a firmware), until the switch is on
 * (restart, recording) or the cable goes (System OFF or the park).
 * Events: SWITCH (lr_log.h); the wake reason is BOOT's RESETREAS (OFF: the switch, VBUS: USB), parked the POWER a 3
 * before it (its RESETREAS SREQ). */
#include "lr_switch.h"

#include <hal/nrf_gpio.h>
#include <hal/nrf_power.h>
#include <zephyr/kernel.h>

#include "led.h"
#include "lr_log.h"
#include "lr_power.h"
#include "wdog_facade.h"

#define SWITCH NRF_GPIO_PIN_MAP(1, 15)  // the other ProMicros leave it open: it would read off, hence the Kconfig
#define BOOT_MS 3000                    // a boot waits this long at most for the switch to settle
#define VBUS_GONE 6                     // polls in a row without VBUS (0.3 s): the cable is out

static bool last;  // lr_switch_debounce's state, from boot on
static int n;
extern bool is_off;  // main.c's status flashes stop while it goes down

static bool on(void)
{
    return !nrf_gpio_pin_read(SWITCH);
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

static void power_off(void)
{
    lr_log(LR_SWITCH, 0, LR_SWITCH_SYSTEM_OFF, 0);
    lr_power_off();
}

void lr_switch_arm(void)
{
    nrf_gpio_cfg_input(SWITCH, NRF_GPIO_PIN_PULLUP);
    k_msleep(1);
    nrf_gpio_cfg_sense_input(SWITCH, NRF_GPIO_PIN_PULLUP, on() ? NRF_GPIO_PIN_SENSE_HIGH : NRF_GPIO_PIN_SENSE_LOW);
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
        lr_power_off();  // no SWITCH event: a lever bouncing in System OFF wakes it again and again, one BOOT each
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
