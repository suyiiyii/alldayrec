// Host check of src/raw_pcm.c (CONFIG_LR_RAW_PCM): every frame lost shows as a gap in seq, whether the mic ring left
// it out (the codec thread behind, src/mic_ring.h) or the 4 KiB queue to the host had no room (the host not reading),
// and what goes out is whole frames with the samples as they were put.
//   d=$(mktemp -d) && mkdir -p $d/zephyr/{drivers,sys} && for h in device kernel drivers/uart sys/ring_buffer; do \
//   touch $d/zephyr/$h.h; done && cc -w -I$d -o $d/raw_pcm_check test/raw_pcm_check.c && $d/raw_pcm_check
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

// --- the Zephyr API raw_pcm.c uses
#define __packed __attribute__((packed))
struct device { int x; };
static struct device cdc_dev;
#define DEVICE_DT_GET(x) (&cdc_dev)
#define DT_NODELABEL(x) 0
struct k_spinlock { int x; };
typedef int k_spinlock_key_t;
static int locked;
static k_spinlock_key_t k_spin_lock(struct k_spinlock *l) { assert(!locked); return ++locked; }
static void k_spin_unlock(struct k_spinlock *l, k_spinlock_key_t k) { assert(locked); --locked; }
#define RB 4096
struct ring_buf { uint8_t buf[RB]; uint32_t put, got; };
#define RING_BUF_DECLARE(name, size) static struct ring_buf name
static uint32_t ring_buf_space_get(struct ring_buf *r) { return RB - (r->put - r->got); }
static uint32_t ring_buf_put(struct ring_buf *r, const uint8_t *p, uint32_t n)
{
    assert(n <= ring_buf_space_get(r));
    for (uint32_t i = 0; i < n; i++) {
        r->buf[r->put++ % RB] = p[i];
    }
    return n;
}
static uint32_t ring_buf_get_claim(struct ring_buf *r, uint8_t **p, uint32_t n)
{
    uint32_t have = r->put - r->got, to_end = RB - r->got % RB;
    n = n < have ? n : have;
    n = n < to_end ? n : to_end;
    *p = r->buf + r->got % RB;
    return n;
}
static int ring_buf_get_finish(struct ring_buf *r, uint32_t n) { assert(n <= r->put - r->got); r->got += n; return 0; }
static void (*tx_cb)(const struct device *, void *);
static bool tx_enabled;
static int kicks;
static uint8_t wire[100000];  // what reached the host
static size_t wire_n;
static void uart_irq_callback_set(const struct device *d, void (*f)(const struct device *, void *)) { tx_cb = f; }
static void uart_irq_tx_enable(const struct device *d) { assert(!locked); tx_enabled = true; kicks++; }
static void uart_irq_tx_disable(const struct device *d) { tx_enabled = false; }
static int uart_fifo_fill(const struct device *d, const uint8_t *p, int n)  // 37 B at a time: partial fills
{
    n = n > 37 ? 37 : n;
    memcpy(wire + wire_n, p, n);
    wire_n += n;
    return n;
}
uint8_t mic_gain(void) { return 0x28; }

#include "../src/mic_ring.h"
#include "../src/raw_pcm.c"

#define FRAME 332  // 12 B head + 160 samples
static void drain(void)  // the host reads everything queued
{
    int max = 100000;
    while (tx_enabled && max--) {
        tx_cb(&cdc_dev, 0);
    }
    assert(max > 0);
}
static uint32_t seq(size_t i) { uint32_t s; memcpy(&s, wire + i * FRAME + 4, 4); return s; }
static int16_t sample0(size_t i) { int16_t v; memcpy(&v, wire + i * FRAME + 12, 2); return v; }
static uint32_t gaps(size_t from, size_t to)
{
    uint32_t g = 0;
    for (size_t i = from + 1; i < to; i++) {
        g += seq(i) - seq(i - 1) - 1;
    }
    return g;
}

static struct mic_ring ring;
static int16_t pcm[160], out[160];

static bool give(int16_t v)  // the mic's next buffer, every sample v
{
    for (int j = 0; j < 160; j++) {
        pcm[j] = v;
    }
    return mic_ring_put(&ring, pcm);
}
static void take(bool host_reads)  // the codec thread: one buffer out of the ring, on to raw_pcm_put
{
    uint32_t left_out;
    assert(mic_ring_get(&ring, out, &left_out));
    raw_pcm_put(out, 160, left_out);
    memset(out, 0, sizeof(out));  // the codec goes on with its buffer: what was queued must not change
    if (host_reads) {
        drain();
    }
}

int main(void)
{
    // the codec thread 1.3 s behind: 130 buffers, the ring holds 100, 30 left out; then one more
    int lost = 0;
    for (int i = 0; i < 130; i++) {
        lost += !give(i);
    }
    assert(lost == 30);
    while (ring.put != ring.got) {
        take(true);
    }
    assert(give(130));
    take(true);
    assert(wire_n == 101 * FRAME);
    assert(gaps(0, 101) == 30 && seq(100) == 130 && sample0(100) == 130);  // the 30 left out are a gap, not hidden
    for (size_t i = 0; i < 101; i++) {
        uint16_t sum = 0, stored;
        for (int j = 12; j < FRAME; j++) {
            sum += wire[i * FRAME + j];
        }
        memcpy(&stored, wire + i * FRAME + 10, 2);
        assert(stored == sum && memcmp(wire + i * FRAME, "LRPC", 4) == 0);
        assert(seq(i) == (uint32_t) sample0(i));  // each frame numbered as the mic numbered its buffer
    }

    // both at once: the host stops reading (the queue holds 12 frames) while the ring leaves 5 out
    size_t start = wire_n / FRAME;
    int first = kicks;
    for (int i = 0; i < 100; i++) {
        assert(give(131 + i));
    }
    for (int i = 0; i < 5; i++) {
        assert(!give(0));  // 231..235 left out
    }
    for (int i = 0; i < 20; i++) {
        take(false);  // 131..150 put, 12 fit
    }
    assert(kicks == first + 1);  // one transfer in flight, no kick for each frame
    drain();
    while (ring.put != ring.got) {
        take(true);  // 151..230, then the next one after the 5 left out
    }
    assert(give(236));
    take(true);
    size_t end = wire_n / FRAME;
    assert(wire_n % FRAME == 0 && end - start == 12 + 80 + 1);
    assert(seq(start) == 131 && seq(end - 1) == 236 && sample0(end - 1) == 236);
    assert(gaps(start, end) == 8 + 5);  // 8 the queue had no room for, 5 the ring left out
    printf("raw_pcm_check ok: ring drops %d -> gap %u; queue full + ring drops -> gap %u\n", lost, gaps(0, 101),
           gaps(start, end));
    return 0;
}
