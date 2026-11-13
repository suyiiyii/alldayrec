/* Power. On battery the battery is read once a minute; three readings in a row under
 * CONFIG_LR_LOW_BATTERY_MV end recording while the cell still holds the board up: a POWER event, the open file closed,
 * the phone told, and a restart into System OFF until USB (VBUS) comes, or LR-P3's switch moves. Without it the X1 ran
 * to ~3.0 V and browned out over and over, a card mount each time; at 3.3 V there were still 25-29 minutes left.
 *
 * LR-N4 and LR-N5 (CONFIG_LR_PARK) park in System ON instead of System OFF: one LR-N4, sent to System OFF at 3.28 V,
 * never came back. 129 boots followed, each over within 0.6 s with VDD at 0.44 V and VDDH there, as if REG0 had
 * let VDD go (REG0 wants VDDH 0.3 V over its 3.0 V); its USB detect is open, so VBUS could not have woken it either.
 * Parked (PS ION_RAMON_RTC 3.16 uA), each second the watchdog is fed and the switch and USB read; after a low battery
 * also CHRG (charging: USB came on a board that cannot see it) and every 10 s the battery. The switch moving, USB,
 * charging or the battery back at LR_PARK_UP_MV restart it (lr_park_wake): the old wakes, the reset reason SREQ.
 *
 * LR-P3 (CONFIG_LR_POWER_TRACE) also keeps a power trail: a POWER record with VDDH every minute, and the chip's
 * power-fail warning (VDDH under 3.3 V or VDD under 2.8 V) as it comes, each written to flash at once (lr_log.c
 * URGENT). A board that loses all power (a battery protection cutting out) keeps its last minute and the voltage. */
#include "lr_power.h"

#include <hal/nrf_gpio.h>
#include <hal/nrf_power.h>
#include <nrfx_power.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/hci.h>
#include <zephyr/kernel.h>

#include "config.h"
#include "lib/battery/battery.h"
#include "lr_log.h"
#include "lr_store.h"
#include "lr_switch.h"
#include "sdcard.h"
#include "transport.h"
#include "wdog_facade.h"

#define LOW_READINGS 3
#define OFF_MAGIC 0x10B477E7
static __noinit uint32_t off_at_boot;  // OFF_MAGIC: the restart was a low battery's (a cold start's RAM fails it)

#ifdef CONFIG_LR_PARK
static bool low_park;  // parked by a low battery (lr_power_init), not by the switch
static bool parked;    // minute() does nothing from here on
static struct k_work_delayable minute_work;  // defined below

/* Parked, nothing runs that System OFF stopped: no POWER record a minute and no power-fail warning (each an urgent
 * flash write: 16 KB of log gone round in ~7 h), no low-battery restart turning a switch-off park into a low one, no
 * log tick (lr_log_stop). Why it parked is in flash already (lr_power_off); the restart's BOOT ends it. */
static void park(void)
{
    parked = true;
    struct k_work_sync sync;
    k_work_cancel_delayable_sync(&minute_work, &sync);  // a minute under way ends first: it could arm the warning again
#ifdef CONFIG_LR_POWER_TRACE
    nrfx_power_pof_disable();
#endif
    lr_log_stop();
#ifdef CONFIG_LR_SWITCH
    bool sw = lr_switch_read();
#endif
    for (uint32_t s = 1;; s++) {
        watchdog_feed();
        k_sleep(K_SECONDS(1));
        bool moved = false, charging = false;
#ifdef CONFIG_LR_SWITCH
        moved = lr_switch_read() != sw;
#endif
#ifdef CHRG_PIN
        charging = low_park && battery_chrg_low();
#endif
        uint16_t mv = 0;
        int err = low_park && s % 10 == 0 ? battery_get_millivolt(&mv) : -EAGAIN;
        if (lr_park_wake(moved, nrf_power_usbregstatus_vbusdet_get(NRF_POWER), low_park, charging, err, mv)) {
            NVIC_SystemReset();
        }
    }
}
#endif

#ifdef CONFIG_LR_POWER_TRACE
static void warn(void);
static const nrfx_power_pofwarn_config_t pof = {
    .handler = warn,
    .thr = NRF_POWER_POFTHR_V28,
    .thrvddh = NRF_POWER_POFTHRVDDH_V33,
};

/* In the POWER interrupt: no ADC here (VDDH 0, not measured); once, the minute arms it again */
static void warn(void)
{
    nrfx_power_pof_disable();
    lr_log(LR_POWER, 0, LR_POWER_WARNING, 0);
}
#endif

void lr_power_stop(void)
{
    lr_store_shutdown();
    struct bt_conn *conn = get_current_connection();
    if (conn) {
        bt_conn_disconnect(conn, BT_HCI_ERR_REMOTE_POWER_OFF);
        k_msleep(200);  // the disconnect goes out before the radio stops
    }
}

/* At boot, or up on USB with the cable gone (lr_switch.c): the radio and the mic's EasyDMA not started, as System OFF
 * wants (the mic's pins never set up). The log to flash first (System OFF keeps no RAM); outputs keep their level. */
void lr_power_off(void)
{
    lr_log_sync();
#if DT_NODE_HAS_PROP(LR_USER, sd_pwr_gpios)
    // VCC off: P0.13 low. On SuperMini clones whose VCC regulator enable has a 5.6k pull-up to VDDH that costs ~0.7 mA
    // (a hardware fix: the resistor); left open, the pull-up turns VCC on
    nrf_gpio_pin_clear(LR_PIN(sd_pwr_gpios));
    nrf_gpio_cfg_output(LR_PIN(sd_pwr_gpios));
    sd_lines_release();  // or they feed the card on through the night
#endif
    nrf_gpio_pin_clear(PDM_PWR_PIN);
    nrf_gpio_cfg_output(PDM_PWR_PIN);
#ifdef CONFIG_LR_PARK
    park();
#endif
#ifdef CONFIG_LR_SWITCH
    lr_switch_arm();
#endif
    NRF_POWER->SYSTEMOFF = 1;  // VBUS rising wakes it too
    for (;;) {
    }
}

uint8_t lr_charge(void)
{
    return lr_charge_state(nrf_power_usbregstatus_vbusdet_get(NRF_POWER), battery_charging());
}

static void minute(struct k_work *w)
{
    static int low;
#ifdef CONFIG_LR_PARK
    if (parked) {  // the run park() waited for may have rescheduled it (a cancel does not stop that)
        return;
    }
#endif
    bool usb = nrf_power_usbregstatus_vbusdet_get(NRF_POWER);  // on USB the reading is the cable's, not the battery's
    uint16_t mv = 0;
    int err = usb ? 0 : battery_get_millivolt(&mv);
#ifdef CONFIG_LR_POWER_TRACE
    if (!usb) {
#ifdef CHRG_PIN
        lr_log(LR_POWER, MIN(mv / 20, 255), LR_POWER_MINUTE, 0x100 | !battery_chrg_low());  // b: CHRG, for USB unseen
#else
        lr_log(LR_POWER, MIN(mv / 20, 255), LR_POWER_MINUTE, 0);
#endif
    }
    nrfx_power_pof_enable(&pof);
#endif
    low = lr_low_count(low, usb, err, mv, CONFIG_LR_LOW_BATTERY_MV);
    if (low >= LOW_READINGS) {
        lr_log(LR_POWER, MIN(mv / 20, 255), LR_POWER_LOW, 0);
        lr_power_stop();
        off_at_boot = OFF_MAGIC;
        lr_log_sync();
        NVIC_SystemReset();
    }
    k_work_reschedule(k_work_delayable_from_work(w), K_MINUTES(1));
}
static K_WORK_DELAYABLE_DEFINE(minute_work, minute);

void lr_power_init(void)
{
    bool off = off_at_boot == OFF_MAGIC;
    off_at_boot = 0;
    if (off && !nrf_power_usbregstatus_vbusdet_get(NRF_POWER)) {  // plugged in meanwhile: no VBUS rising would wake it
#ifdef CONFIG_LR_PARK
        low_park = true;
#endif
        lr_power_off();
    }
#ifdef CONFIG_LR_POWER_TRACE
    nrfx_power_pof_init(&pof);
    nrfx_power_pof_enable(&pof);
#endif
    k_work_schedule(&minute_work, K_MINUTES(1));
}
