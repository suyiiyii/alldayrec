/* LR-N5-S's buzzer segment (buzzer.c; the hardware's FW-TODO-R31 FW-2 / FW-3, from the Pro r3.1 review p.6 §4.3 and
 * p.11 §7): one PWM sequence of whole 4 kHz periods, at most 20 ms of tone, then one period low, played once. The
 * duty is the safe tier only: never above 25 %, and the build's cap (CONFIG_LR_BUZZER_DUTY_MAX) when lower. */
#ifndef BUZZER_SEQ_H
#define BUZZER_SEQ_H
#include <stdint.h>

#define BUZ_TOP 4000                                /* COUNTERTOP at 16 MHz: 4 kHz, KLJ-4020's rated frequency */
#define BUZ_SEG_MS 20                               /* the longest segment: the project's start point, not a vendor figure */
#define BUZ_PERIODS (BUZ_SEG_MS * 16000 / BUZ_TOP)  /* 80 periods of 0.25 ms */
#define BUZ_SAFE_PERMILLE 250                       /* 25 %: the first version's only tier */
/* bit 15 set: high for the compare count from the start of each period, then low; compare 0 is low all period
 * (Zephyr's drivers/pwm/pwm_nrfx.c, PWM_NRFX_CH_VALUE for normal polarity) */
#define BUZ_POL (1u << 15)

/* Fill seq (BUZ_PERIODS + 1 values) at min(cap, 25 %); its last value is a true low. The count to play, 0: silent */
static inline int buz_fill(uint16_t *seq, int cap_permille)
{
    int d = cap_permille < BUZ_SAFE_PERMILLE ? cap_permille : BUZ_SAFE_PERMILLE;

    if (d <= 0) {
        return 0;
    }
    for (int i = 0; i < BUZ_PERIODS; i++) {
        seq[i] = BUZ_POL | (uint16_t)(BUZ_TOP * d / 1000);
    }
    seq[BUZ_PERIODS] = BUZ_POL;
    return BUZ_PERIODS + 1;
}
#endif
