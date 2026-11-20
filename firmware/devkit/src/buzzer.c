/* LR-N5-S's buzzer on P0.15 (buz-gpios), through C69 to Q2's gate (the hardware's PINS-N5S.md BUZ): PWM only, never a
 * level. FW-TODO-R31 FW-2 (Pro r3.1 review p.11 §7), step by step:
 *   1. GPIO OUT 0 on P0.15 from PRE_KERNEL_1 on: when the PWM is stopped or disabled the pin is GPIO, low.
 *   2. A finite sequence in static RAM (buzzer_seq.h), written only here and only while the PWM is off: CNT values,
 *      REFRESH 0, ENDDELAY 0, LOOP 0: at most 80 periods of 0.25 ms plus one low = 20.25 ms.
 *   3. SHORTS SEQEND0 -> STOP: the hardware ends the segment, not a CPU callback.
 *   4. No LOOPSDONE -> SEQSTART, no PPI, no timer: nothing starts the next segment but buzzer_segment().
 *   5. The next segment only from the CPU, running, once the last one has stopped. The supply is not read: the safe
 *      tier's duty (FW-3) is the one for VSYS 5.5 V, the most it can be, on battery and USB alike; a 50 % tier would
 *      need VSYS read again before each segment and is not built.
 *   6. The last value is a true low, and STOP returns the pin to OUT 0.
 * Covers a CPU that stops serving with the PWM's registers and RAM intact; not two independent safety channels. */
#include "buzzer.h"

#include <errno.h>
#include <hal/nrf_gpio.h>
#include <zephyr/init.h>
#include <zephyr/kernel.h>

#include "buzzer_seq.h"
#include "config.h"

#define PWM NRF_PWM2  // not in the devicetree (pwm0 is the XIAO's LED), so no driver touches it
static uint16_t seq[BUZ_PERIODS + 1];

static int buzzer_init(void)
{
    nrf_gpio_pin_clear(BUZ_PIN);
    nrf_gpio_cfg_output(BUZ_PIN);
    PWM->ENABLE = 0;
    PWM->PSEL.OUT[0] = BUZ_PIN;
    PWM->MODE = PWM_MODE_UPDOWN_Up;
    PWM->PRESCALER = PWM_PRESCALER_PRESCALER_DIV_1;
    PWM->COUNTERTOP = BUZ_TOP;
    PWM->LOOP = 0;
    PWM->DECODER = PWM_DECODER_LOAD_Common | PWM_DECODER_MODE_RefreshCount << PWM_DECODER_MODE_Pos;
    PWM->SEQ[0].REFRESH = 0;
    PWM->SEQ[0].ENDDELAY = 0;
    PWM->SEQ[1].CNT = 0;
    PWM->SHORTS = PWM_SHORTS_SEQEND0_STOP_Msk;
    return 0;
}
SYS_INIT(buzzer_init, PRE_KERNEL_1, 0);

int buzzer_segment(void)
{
    if (PWM->ENABLE) {
        return -EBUSY;
    }
    int n = buz_fill(seq, CONFIG_LR_BUZZER_DUTY_MAX);

    if (!n) {
        return -EPERM;
    }
    PWM->SEQ[0].PTR = (uint32_t)seq;
    PWM->SEQ[0].CNT = n;
    PWM->EVENTS_STOPPED = 0;
    PWM->ENABLE = 1;
    PWM->TASKS_SEQSTART[0] = 1;
    for (int i = 0; i < BUZ_SEG_MS + 10 && !PWM->EVENTS_STOPPED; i++) {
        k_msleep(1);
    }
    bool stopped = PWM->EVENTS_STOPPED;

    if (!stopped) {
        PWM->TASKS_STOP = 1;
        for (int i = 0; i < 2 && !PWM->EVENTS_STOPPED; i++) {
            k_msleep(1);
        }
    }
    PWM->ENABLE = 0;
    return stopped ? 0 : -ETIMEDOUT;
}

int buzzer_beep(int ms)
{
    for (int t = 0; t < ms; t += BUZ_SEG_MS) {
        int e = buzzer_segment();

        if (e) {
            return e;
        }
    }
    return 0;
}
