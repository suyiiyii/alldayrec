#include "mic.h"

#include <haly/nrfy_gpio.h>
#include <zephyr/device.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/settings/settings.h>

#include "config.h"
#include "led.h"
#include <zephyr/drivers/clock_control/nrf_clock_control.h>
#include "nrfx_pdm.h"
#include "utils.h"

LOG_MODULE_REGISTER(mic, CONFIG_LOG_DEFAULT_LEVEL);

//
// Port of this code: https://github.com/Seeed-Studio/Seeed_Arduino_Mic/blob/master/src/hardware/nrf52840_adc.cpp
//

static int16_t _buffer_0[MIC_BUFFER_SAMPLES];
static int16_t _buffer_1[MIC_BUFFER_SAMPLES];
static volatile uint8_t _next_buffer_index = 0;
static volatile mix_handler _callback = NULL;
uint32_t pdm_errors;  // lr_log.c's HEALTH: counted here, cleared there every 10 minutes

static void pdm_irq_handler(nrfx_pdm_evt_t const *event)
{
    // Ignore error (how to handle?)
    if (event->error) {
        pdm_errors++;
        LOG_ERR("PDM error: %d", event->error);
        return;
    }

    // Assign buffer
    if (event->buffer_requested) {
        LOG_DBG("Audio buffer requested");
        if (_next_buffer_index == 0) {
            nrfx_pdm_buffer_set(_buffer_0, MIC_BUFFER_SAMPLES);
            _next_buffer_index = 1;
        } else {
            nrfx_pdm_buffer_set(_buffer_1, MIC_BUFFER_SAMPLES);
            _next_buffer_index = 0;
        }
    }

    // Release buffer
    if (event->buffer_released) {
        LOG_DBG("Audio buffer requested");
        if (_callback) {
            _callback(event->buffer_released);
        }
    }
}

// Direct (no Zephyr wrapper), but a released buffer wakes the codec thread (codec_receive_pcm): check for a reschedule
ISR_DIRECT_DECLARE(pdm_isr)
{
    nrfx_pdm_irq_handler();
    ISR_DIRECT_PM();
    return 1;
}

// Hold the crystal (HFXO) for as long as the mic runs. The PDM clock is divided from HFCLK; a bare HFCLKSTART
// is undone whenever the BLE stack (MPSL) releases its own request, and HFCLK falls back to the RC oscillator:
// on battery (no USB holding HFXO) frames came ~1% fast, 36 s an hour off the frame-clock timestamps.
static void hfxo_hold(void)
{
    static struct onoff_client hfxo;
    sys_notify_init_spinwait(&hfxo.notify);
    int err = onoff_request(z_nrf_clock_control_get_onoff(CLOCK_CONTROL_NRF_SUBSYS_HF), &hfxo);
    int res;
    while (err >= 0 && sys_notify_fetch_result(&hfxo.notify, &res) == -EAGAIN) {
        k_msleep(1);
    }
    if (err < 0) {
        LOG_ERR("HFXO request failed: %d", err);
    }
}

/* The PDM gain in force (GAINL = GAINR), kept in NVS: CTRL 0x21 sets it (lr_store.c), every boot starts with it */
#define GAIN_KEY "lr/mic_gain"
static uint8_t gain = MIC_GAIN;

static int gain_loaded(const char *key, size_t len, settings_read_cb read, void *arg, void *param)
{
    uint8_t g;
    if (len == 1 && read(arg, &g, 1) == 1 && g <= NRF_PDM_GAIN_MAXIMUM) {
        gain = g;
    }
    return 0;
}

uint8_t mic_gain(void)
{
    return gain;
}

int mic_set_gain(uint8_t g)
{
    if (g > NRF_PDM_GAIN_MAXIMUM) {
        return -EINVAL;
    }
    if (g == gain) {
        return 0;  // not written again: the settings partition is small
    }
    int err = settings_save_one(GAIN_KEY, &g, 1);  // kept first: the gain in force is always the one the next boot takes
    if (err) {
        return err;
    }
    gain = g;
    // into the running PDM, not stopped and started: that would break the frame clock the stream's times hang on
    nrf_pdm_gain_set(NRF_PDM0, g, g);
    LOG_INF("mic gain 0x%02x", g);
    return 0;
}

int mic_start()
{
    hfxo_hold();
    settings_subsys_init();
    settings_load_subtree_direct(GAIN_KEY, gain_loaded, NULL);

    // Configure PDM
    nrfx_pdm_config_t pdm_config = NRFX_PDM_DEFAULT_CONFIG(PDM_CLK_PIN, PDM_DIN_PIN);
    pdm_config.gain_l = gain;
    pdm_config.gain_r = gain;
    pdm_config.interrupt_priority = MIC_IRC_PRIORITY;
    pdm_config.clock_freq = NRF_PDM_FREQ_1280K;
    pdm_config.mode = NRF_PDM_MODE_MONO;
    pdm_config.edge = NRF_PDM_EDGE_LEFTFALLING;
    pdm_config.ratio = NRF_PDM_RATIO_80X;
    IRQ_DIRECT_CONNECT(PDM_IRQn, 5, pdm_isr, 0); // IMPORTANT!
    if (nrfx_pdm_init(&pdm_config, pdm_irq_handler) != NRFX_SUCCESS) {
        LOG_ERR("Audio unable to initialize PDM");
        return -1;
    }

    // Power on Mic
    nrfy_gpio_cfg_output(PDM_PWR_PIN);
    nrfy_gpio_pin_set(PDM_PWR_PIN);

    // Start PDM
    if (nrfx_pdm_start() != NRFX_SUCCESS) {
        LOG_ERR("Audio unable to start PDM");
        return -1;
    }

    LOG_INF("Audio microphone started");
    return 0;
}

void set_mic_callback(mix_handler callback)
{
    _callback = callback;
}

void mic_off()
{
    nrfy_gpio_pin_clear(PDM_PWR_PIN);
}

void mic_on()
{
    nrfy_gpio_pin_set(PDM_PWR_PIN);
}

#ifdef CONFIG_LR_POWER_DEBUG
bool lr_debug_mic_off;

/* Power tests: the PDM stopped, the mic's supply and the crystal it holds let go, and back */
void mic_debug(bool on)
{
    if (on != lr_debug_mic_off) {
        return;
    }
    lr_debug_mic_off = !on;
    if (on) {
        hfxo_hold();
        nrfy_gpio_pin_set(PDM_PWR_PIN);
        nrfx_pdm_start();
    } else {
        nrfx_pdm_stop();
        k_msleep(20);  // the buffer under way ends (nrfx stops at its end)
        nrfy_gpio_pin_clear(PDM_PWR_PIN);
        onoff_release(z_nrf_clock_control_get_onoff(CLOCK_CONTROL_NRF_SUBSYS_HF));
    }
}
#endif
