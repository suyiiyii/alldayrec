/*
 * CONFIG_LR_RAW_PCM, tests only, never released (LR_RAW_PCM=1 ./lr.sh build V): every 10 ms mic buffer as the PDM gave
 * it (before the high-pass and Opus) on the USB serial port, for lr_pcm.py. Level-cal E1: is the PDM's GAIN applied
 * before or after its 16-bit rounding, seen in the raw sample values. A frame: "LRPC", seq u32 (gaps: frames lost,
 * in the mic ring or here), the gain in force u8, 0, the byte sum of the samples u16, 160 samples s16, all little-endian. Nothing
 * reading the port: the frames are dropped here (read from the code, not seen on a board: the class waits on the
 * one transfer in flight, nothing spins).
 */
#include <zephyr/device.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/ring_buffer.h>

#include "mic.h"

static const struct device *const cdc = DEVICE_DT_GET(DT_NODELABEL(usb_cdc_acm_uart));
RING_BUF_DECLARE(raw_rb, 4096);  // ~12 frames: what waits for the host
static struct k_spinlock raw_lock;
static uint32_t raw_seq;
static bool tx_on;  // a transfer in flight or about to be: the next comes from its completion, not from a new frame

/* The CDC class's workqueue (the only place uart_fifo_fill works), at a kick or a transfer's completion. Nothing
 * left: TX off, or the class calls this again at once for as long as its own FIFO is empty (usbd_cdc_acm.c). Only one
 * transfer at a time: the class's pool has 2 buffers (one is RX's), and its TX work finding none resubmits itself, a
 * busy loop on a cooperative queue while the host is not reading */
static void raw_tx(const struct device *dev, void *user)
{
    k_spinlock_key_t k = k_spin_lock(&raw_lock);
    uint8_t *p;
    uint32_t n = ring_buf_get_claim(&raw_rb, &p, 512);
    if (n) {
        ring_buf_get_finish(&raw_rb, uart_fifo_fill(dev, p, n));
    } else {
        uart_irq_tx_disable(dev);
        tx_on = false;
    }
    k_spin_unlock(&raw_lock, k);
}

/* The codec thread, each buffer as it takes it, with the mic ring's buffers left out just before it (a gap in seq):
 * a whole frame or none, never waits. Host check: test/raw_pcm_check.c */
void raw_pcm_put(const int16_t *pcm, size_t n, uint32_t left_out)
{
    static bool started;
    if (!started) {
        uart_irq_callback_set(cdc, raw_tx);
        started = true;
    }
    raw_seq += left_out;
    struct __packed {
        char magic[4];
        uint32_t seq;
        uint8_t gain, zero;
        uint16_t sum;
    } h = {{'L', 'R', 'P', 'C'}, raw_seq++, mic_gain(), 0, 0};
    const uint8_t *b = (const uint8_t *) pcm;
    for (size_t i = 0; i < n * 2; i++) {
        h.sum += b[i];
    }
    k_spinlock_key_t k = k_spin_lock(&raw_lock);
    bool kick = false;
    if (ring_buf_space_get(&raw_rb) >= sizeof(h) + n * 2) {
        ring_buf_put(&raw_rb, (const uint8_t *) &h, sizeof(h));
        ring_buf_put(&raw_rb, b, n * 2);
        kick = !tx_on;
        tx_on = true;
    }
    k_spin_unlock(&raw_lock, k);
    // after the unlock: it submits the class's work, which may yield to it. raw_tx turns TX off only under the lock,
    // seeing the ring empty, so this frame is never left waiting. A transfer cancelled (unplugged) leaves tx_on set:
    // the class's enable at the next plug-in calls raw_tx again, TX still on
    if (kick) {
        uart_irq_tx_enable(cdc);
    }
}
