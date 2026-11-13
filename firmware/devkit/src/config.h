#include <haly/nrfy_gpio.h>

// #define SAMPLE_RATE 16000
// PDM GAINL/GAINR (mic.c): 0x28 = 0 dB, 0.5 dB a step, 0x00 = -20 dB .. 0x50 = +20 dB (nrf52840_bitfields.h).
// Not 0x40 (+12 dB, full scale near 105 dB SPL): in 10-03 device audio 36-37% of seconds peaked above -1 dBFS, and
// clipping costs ASR (a sample clipped at 32%: CER 19.6% -> 30.4%). Not lower either: at -12 dB quiet voices lose words
#define MIC_GAIN 0x28
#define MIC_HPF_HZ 80              // 4th-order Butterworth high-pass before the codec: the PDM path carries 2-20 Hz at up to -8 dBFS
#define MIC_IRC_PRIORITY 7
// 10 ms, one codec frame: each frame is encoded as it comes (~6 ms of CPU). With 100 ms buffers the codec encoded ten
// back to back, the CPU busy ~65 of every 100 ms, and on battery that load reached the mic: the 2-7.5 kHz noise floor
// in those 65 ms was up to 1.7x (+2.4 dB) the rest's, an audible 10 Hz flutter in a quiet room. On USB it does not show.
#define MIC_BUFFER_SAMPLES 160
#define AUDIO_BUFFER_SAMPLES 16000 // 1s
#define NETWORK_RING_BUF_SIZE 32   // number of frames * CODEC_OUTPUT_MAX_BYTES
#define MINIMAL_PACKET_SIZE 100    // Less than that doesn't make sence to send anything at all

// PIN definitions
// https://github.com/Seeed-Studio/Adafruit_nRF52_Arduino/blob/5aa3573913449410fd60f76b75673c53855ff2ec/variants/Seeed_XIAO_nRF52840_Sense/variant.cpp#L34
#include <zephyr/devicetree.h>
#define LR_USER DT_PATH(zephyr_user)
#if DT_NODE_HAS_PROP(LR_USER, pdm_clk_gpios)  // an external PDM mic, wired per a board overlay (lr_promicro.overlay)
#define LR_PIN(prop) NRF_GPIO_PIN_MAP(DT_PROP(DT_GPIO_CTLR(LR_USER, prop), port), DT_GPIO_PIN(LR_USER, prop))
#define PDM_DIN_PIN LR_PIN(pdm_din_gpios)
#define PDM_CLK_PIN LR_PIN(pdm_clk_gpios)
#define PDM_PWR_PIN LR_PIN(pdm_pwr_gpios)
#if DT_NODE_HAS_PROP(LR_USER, chrg_gpios)  // the charger's status pin (LR-N4's TP4054 CHRG): battery.c, lr_power.c
#define CHRG_PIN LR_PIN(chrg_gpios)
#endif
#else  // the XIAO nRF52840 Sense's own mic
#define PDM_DIN_PIN NRF_GPIO_PIN_MAP(0, 16)
#define PDM_CLK_PIN NRF_GPIO_PIN_MAP(1, 0)
#define PDM_PWR_PIN NRF_GPIO_PIN_MAP(1, 10)
#endif

// Codecs
#ifdef CONFIG_OMI_CODEC_OPUS
#define CODEC_OPUS 1
#else
#error "Enable CONFIG_OMI_CODEC_OPUS in the project .conf file"
#endif

#if CODEC_OPUS
#define CODEC_PACKAGE_SAMPLES 160
#define CODEC_OUTPUT_MAX_BYTES CODEC_PACKAGE_SAMPLES * 2 // Let's assume that 16bit is enough
#define CODEC_OPUS_APPLICATION OPUS_APPLICATION_RESTRICTED_LOWDELAY
#define CODEC_OPUS_BITRATE 32000
#define CODEC_OPUS_VBR 1 // Or 1
#define CODEC_OPUS_COMPLEXITY 3
#endif
#define CONFIG_OPUS_MODE CONFIG_OPUS_MODE_CELT

// Codec IDs

#ifdef CODEC_OPUS
#define CODEC_ID 20
#endif

// Logs
// #define LOG_DISCARDED