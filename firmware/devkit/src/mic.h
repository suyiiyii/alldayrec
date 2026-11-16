#ifndef MIC_H
#define MIC_H

#include <stdbool.h>
#include <stdint.h>

typedef void (*mix_handler)(int16_t *);

/**
 * @brief Initialize the Microphone
 *
 * Initializes the Microphone
 *
 * @return 0 if successful, negative errno code if error
 */
int mic_start();
void set_mic_callback(mix_handler _callback);

/* The PDM gain (GAINL = GAINR, mic_gain.c): 0x28 = 0 dB, 0.5 dB a step, 0x00 = -20 dB .. 0x50 = +20 dB (config.h
 * MIC_GAIN the default). Set: into the running PDM and the event log at once, never waits; into NVS on the system
 * workqueue after, mic_gain_kept() false until it took. -EINVAL past 0x50. */
uint8_t mic_gain(void);
bool mic_gain_kept(void);
int mic_set_gain(uint8_t gain);
void mic_off();
void mic_on();
/* CONFIG_LR_POWER_DEBUG: the mic (and the crystal it holds) on and off */
void mic_debug(bool on);
#endif
