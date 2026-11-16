// Host check of the mic's gain in src/mic_gain.c: CTRL GAIN puts it in force and in the event log at once, NVS takes it
// later off the BT RX thread (a failed save logged and shown, a same-value GAIN saves it again), and a record NVS keeps
// that fails its check (a flipped bit, the 1-byte draft) puts the default in force with an event. And src/mic.c's PDM
// init at boot: a GAIN on the BT RX thread while it runs is not undone by the gain it read.
//   d=$(mktemp -d) && mkdir -p $d/zephyr/settings $d/zephyr/logging $d/zephyr/drivers/clock_control $d/hal $d/haly && \
//   touch $d/zephyr/{kernel,logging/log,devicetree,device,drivers/gpio,drivers/clock_control/nrf_clock_control}.h \
//   $d/zephyr/settings/settings.h $d/hal/nrf_pdm.h $d/haly/nrfy_gpio.h $d/nrfx_pdm.h && cc -w -pthread -I$d -o \
//   $d/gain_check test/gain_check.c && $d/gain_check
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/types.h>

// --- what mic_gain.c (and config.h) use
#define CONFIG_OMI_CODEC_OPUS 1
#define DT_PATH(x) 0
#define DT_NODE_HAS_PROP(n, p) 0
#define LOG_MODULE_REGISTER(...)
#define LOG_INF(...)
#define NRF_PDM_GAIN_MAXIMUM 0x50
#define NRF_PDM0 0
static int gainl = 0x28, gainr = 0x28;  // the registers
static void nrf_pdm_gain_set(int pdm, uint8_t l, uint8_t r) { gainl = l, gainr = r; }

typedef ssize_t (*settings_read_cb)(void *arg, void *data, size_t len);
#define SETTINGS_STATIC_HANDLER_DEFINE(...)
static uint8_t nvs[8];  // the key's record
static size_t nvs_len;
static int saves, save_err;
static int settings_save_one(const char *key, const void *v, size_t len)
{
    assert(!strcmp(key, "lr/mic_gain"));
    saves++;
    if (save_err) {
        return save_err;
    }
    memcpy(nvs, v, nvs_len = len);
    return 0;
}
static ssize_t nvs_read(void *arg, void *data, size_t len)
{
    memcpy(data, nvs, len < nvs_len ? len : nvs_len);
    return len < nvs_len ? len : nvs_len;
}

struct k_work {
    void (*fn)(struct k_work *);
};
#define K_WORK_DEFINE(name, f) struct k_work name = {f}  // after "static"
static struct k_work *submitted;  // the system workqueue, run by run_work()
static int k_work_submit(struct k_work *w)
{
    submitted = w;
    return 1;
}
static void run_work(void)
{
    struct k_work *w = submitted;
    submitted = NULL;
    if (w) {
        w->fn(w);
    }
}

static struct {
    uint8_t code, x;
    uint32_t a, b;
} logged[16];
static int n_logged, syncs;
void lr_log(uint8_t code, uint8_t x, uint32_t a, uint32_t b)
{
    logged[n_logged++ % 16] = (typeof(logged[0])){code, x, a, b};
}
void lr_log_sync(void) { syncs++; }
#define LAST logged[(n_logged - 1) % 16]

// The BT RX thread is a real thread and the lock a real mutex: a GAIN that does not take the lock writes the registers
// before the init does, and the init's older gain stays in force
#include <pthread.h>
#include <sched.h>
#include <stdatomic.h>
struct k_mutex {
    pthread_mutex_t m;
};
#define K_MUTEX_DEFINE(name) struct k_mutex name = {PTHREAD_MUTEX_INITIALIZER}
#define K_FOREVER 0
static _Thread_local bool on_rx;
static atomic_int rx_at;  // 1: the BT RX thread is at a k_mutex_lock(), 2: its GAIN returned
static void (*at_lock)(void);  // the BT RX thread's turn just before the main thread takes a lock
static int k_mutex_lock(struct k_mutex *m, int t)
{
    void (*f)(void) = at_lock;
    at_lock = NULL;
    if (f) {
        f();
    }
    if (on_rx) {
        atomic_store(&rx_at, 1);
    }
    return pthread_mutex_lock(&m->m);
}
static int k_mutex_unlock(struct k_mutex *m) { return pthread_mutex_unlock(&m->m); }

#include "../src/mic_gain.c"

// --- what mic.c uses: nrfx_pdm_init() writes GAINL/GAINR from the config it was given
#define LED_H
#define UTILS_H
#define NRF_GPIO_PIN_MAP(p, n) ((p) * 32 + (n))
#define LOG_ERR(...)
#define LOG_DBG(...)
#define ISR_DIRECT_DECLARE(f) static int f(void)
#define ISR_DIRECT_PM()
#define IRQ_DIRECT_CONNECT(...)
struct onoff_client {
    int notify;
};
#define sys_notify_init_spinwait(n)
#define onoff_request(m, c) 0
#define sys_notify_fetch_result(n, r) 0
#define z_nrf_clock_control_get_onoff(s) 0
#define k_msleep(ms)
#define NRF_PDM_FREQ_1280K 0
#define NRF_PDM_MODE_MONO 0
#define NRF_PDM_EDGE_LEFTFALLING 0
#define NRF_PDM_RATIO_80X 0
#define NRFX_SUCCESS 0
typedef int nrfx_err_t;
typedef struct {
    int16_t *buffer_released;
    bool buffer_requested;
    int error;
} nrfx_pdm_evt_t;
typedef struct {
    int gain_l, gain_r, interrupt_priority, clock_freq, mode, edge, ratio;
} nrfx_pdm_config_t;
#define NRFX_PDM_DEFAULT_CONFIG(clk, din) (nrfx_pdm_config_t){0}
static void nrfy_gpio_cfg_output(int pin) {}
static void nrfy_gpio_pin_set(int pin) {}
static void nrfy_gpio_pin_clear(int pin) {}
static void nrfx_pdm_buffer_set(int16_t *b, int n) {}
static void nrfx_pdm_irq_handler(void) {}
static nrfx_err_t nrfx_pdm_start(void) { return 0; }
static void (*in_init)(void);  // a GAIN on the BT RX thread once nrfx_pdm_init() runs, after mic_start() read the gain
static pthread_t rx;
static void *rx_run(void *arg)
{
    on_rx = true;
    in_init();
    atomic_store(&rx_at, 2);
    return NULL;
}
static nrfx_err_t nrfx_pdm_init(nrfx_pdm_config_t const *c, void (*handler)(nrfx_pdm_evt_t const *))
{
    if (in_init) {
        atomic_store(&rx_at, 0);
        assert(!pthread_create(&rx, NULL, rx_run, NULL));
        while (!atomic_load(&rx_at)) {  // until it waits on the lock, or (no lock) has written the registers
            sched_yield();
        }
    }
    nrf_pdm_gain_set(NRF_PDM0, c->gain_l, c->gain_r);
    return 0;
}

#include "../src/mic.c"

static void gain_34(void) { assert(mic_set_gain(0x34) == 0); }

static void boot_with(const uint8_t *r, size_t len)  // a boot's settings_load() with NVS holding r
{
    gain = MIC_GAIN, unkept = false;
    memcpy(nvs, r, nvs_len = len);
    gain_loaded(NULL, len, nvs_read, NULL);
}

int main(void)
{
    // nothing in NVS (no call at boot): the default, kept
    assert(mic_gain() == 0x28 && mic_gain_kept());

    // a change: in the registers and the event log (old, new) at once, NVS not touched on the caller's thread
    assert(mic_set_gain(0x34) == 0);
    assert(gainl == 0x34 && gainr == 0x34 && mic_gain() == 0x34);
    assert(n_logged == 1 && LAST.code == 38 && LAST.x == 0 && LAST.a == 0x28 && LAST.b == 0x34);
    assert(saves == 0 && !mic_gain_kept() && submitted);
    run_work();  // the system workqueue: kept as {g, ~g}, the event log flushed
    assert(saves == 1 && nvs_len == 2 && nvs[0] == 0x34 && nvs[1] == 0xCB && mic_gain_kept() && syncs == 1);

    // the same value again: nothing, no event, no flash
    assert(mic_set_gain(0x34) == 0 && !submitted && n_logged == 1);
    // refused past 0x50: nothing changes
    assert(mic_set_gain(0x51) == -EINVAL && mic_gain() == 0x34 && gainl == 0x34 && !submitted && n_logged == 1);

    // two changes before the work runs: one save, of the newest
    assert(mic_set_gain(0x10) == 0 && mic_set_gain(0x50) == 0 && gainl == 0x50);
    assert(n_logged == 3 && LAST.a == 0x10 && LAST.b == 0x50);
    run_work();
    assert(saves == 2 && nvs[0] == 0x50 && mic_gain_kept());

    // NVS does not take it: in force all the same, an event, TIME's flag stays; the same value again saves it again
    save_err = -EIO;
    assert(mic_set_gain(0x20) == 0 && gainl == 0x20);
    run_work();
    assert(saves == 3 && !mic_gain_kept() && nvs[0] == 0x50);
    assert(LAST.code == 38 && LAST.x == 1 && (int32_t) LAST.a == -EIO && LAST.b == 0x20);
    save_err = 0;
    int n = n_logged;
    assert(mic_set_gain(0x20) == 0 && submitted && n_logged == n);  // no change event: the gain did not change
    run_work();
    assert(saves == 4 && nvs[0] == 0x20 && nvs[1] == 0xDF && mic_gain_kept());

    // boot: a good record goes in force, no event
    n = n_logged;
    boot_with((uint8_t[]){0x30, 0xCF}, 2);
    assert(mic_gain() == 0x30 && mic_gain_kept() && n_logged == n);
    // a bit flipped in a value still in range (0x28 -> 0x20): fails the pair, the default, an event, not kept
    boot_with((uint8_t[]){0x20, 0xD7}, 2);
    assert(mic_gain() == 0x28 && !mic_gain_kept());
    assert(n_logged == n + 1 && LAST.code == 38 && LAST.x == 2 && LAST.a == 2 && LAST.b == (0x20 | 0xD7 << 8));
    // the flip in the complement instead, and a pair past 0x50: the same
    boot_with((uint8_t[]){0x28, 0xD6}, 2);
    assert(mic_gain() == 0x28 && !mic_gain_kept() && n_logged == n + 2);
    boot_with((uint8_t[]){0x60, 0x9F}, 2);
    assert(mic_gain() == 0x28 && !mic_gain_kept() && n_logged == n + 3);
    // the 1-byte record of the first draft (never released): not taken
    boot_with((uint8_t[]){0x30}, 1);
    assert(mic_gain() == 0x28 && !mic_gain_kept() && n_logged == n + 4 && LAST.a == 1 && LAST.b == 0x30);
    // a GAIN of the default writes a good record again
    assert(mic_set_gain(0x28) == 0 && submitted);
    run_work();
    assert(nvs_len == 2 && nvs[0] == 0x28 && nvs[1] == 0xD7 && mic_gain_kept());

    // boot's PDM init with NVS's 0x30, a GAIN 0x34 taken (logged, saved) while it runs: the registers end at 0x34
    boot_with((uint8_t[]){0x30, 0xCF}, 2);
    gainl = gainr = 0;
    in_init = gain_34;
    assert(mic_start() == 0 && !pthread_join(rx, NULL));
    in_init = NULL;
    assert(rx_at == 2 && LAST.code == 38 && LAST.x == 0 && LAST.a == 0x30 && LAST.b == 0x34);
    run_work();
    assert(mic_gain() == 0x34 && gainl == 0x34 && gainr == 0x34 && nvs[0] == 0x34 && mic_gain_kept());
    // a GAIN just before the init takes the lock, and one before mic_start(): the init takes that one
    boot_with((uint8_t[]){0x30, 0xCF}, 2);
    at_lock = gain_34;
    assert(mic_start() == 0);
    run_work();
    assert(mic_gain() == 0x34 && gainl == 0x34 && gainr == 0x34 && nvs[0] == 0x34 && mic_gain_kept());
    boot_with((uint8_t[]){0x30, 0xCF}, 2);
    gain_34();
    assert(mic_start() == 0);
    run_work();
    assert(mic_gain() == 0x34 && gainl == 0x34 && gainr == 0x34 && nvs[0] == 0x34 && mic_gain_kept());
    printf("gain_check ok\n");
    return 0;
}
