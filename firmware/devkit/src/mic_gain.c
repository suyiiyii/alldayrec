#include "mic.h"

#include <hal/nrf_pdm.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/settings/settings.h>

#include "config.h"
#include "lr_log.h"

LOG_MODULE_REGISTER(mic_gain, CONFIG_LOG_DEFAULT_LEVEL);

/*
 * The PDM gain in force (GAINL = GAINR). CTRL 0x21 sets it (lr_store.c) on the BT RX thread: into the registers and
 * the event log there, into NVS later on the system workqueue (flash waits for radio slots, NVS may collect garbage
 * first: the RX thread must not wait on that). NVS keeps it as {g, ~g}: a bit flipped anywhere fails the pair, and the
 * default goes in force. A 1-byte record (this change's first draft, never released) fails it too.
 */
#define GAIN_KEY "lr/mic_gain"
static uint8_t gain = MIC_GAIN;
static bool unkept;  // the gain in force is not the one NVS keeps: its save still to come or failed, or NVS's was bad

/* At boot, by transport.c's settings_load(): before the radio is up, so a GAIN written then is not undone here */
static int gain_loaded(const char *key, size_t len, settings_read_cb read, void *arg)
{
    uint8_t r[4] = {0};
    ssize_t n = read(arg, r, len < sizeof(r) ? len : sizeof(r));
    if (len == 2 && n == 2 && r[0] == (uint8_t) ~r[1] && r[0] <= NRF_PDM_GAIN_MAXIMUM) {
        gain = r[0];
        return 0;
    }
    gain = MIC_GAIN;
    unkept = true;  // TIME says so, and a GAIN of the default writes a good record again
    lr_log(LR_GAIN, 2, len, r[0] | r[1] << 8 | r[2] << 16 | (uint32_t) r[3] << 24);
    return 0;
}
SETTINGS_STATIC_HANDLER_DEFINE(mic_gain, GAIN_KEY, NULL, gain_loaded, NULL, NULL);

static void save(struct k_work *w)
{
    uint8_t g = gain;
    int err = settings_save_one(GAIN_KEY, (uint8_t[]){g, (uint8_t) ~g}, 2);
    if (err) {
        lr_log(LR_GAIN, 1, err, g);
    }
    unkept = err || g != gain;  // changed meanwhile: this work is queued again for it
    lr_log_sync();  // the change's record (and a failure's) kept at once, not in up to 5 minutes
}
static K_WORK_DEFINE(save_work, save);

uint8_t mic_gain(void)
{
    return gain;
}

bool mic_gain_kept(void)
{
    return !unkept;
}

int mic_set_gain(uint8_t g)
{
    if (g > NRF_PDM_GAIN_MAXIMUM) {
        return -EINVAL;
    }
    if (g != gain) {
        uint8_t old = gain;
        gain = g;
        // into the running PDM, not stopped and started: that would break the frame clock the stream's times hang
        // on. Its interrupt never writes GAINL/GAINR (nrfx_pdm.c)
        nrf_pdm_gain_set(NRF_PDM0, g, g);
        lr_log(LR_GAIN, 0, old, g);
        LOG_INF("mic gain 0x%02x", g);
    } else if (!unkept) {
        return 0;  // not written again: the settings partition is small
    }
    unkept = true;
    k_work_submit(&save_work);
    return 0;
}
