#include "lr_log.h"

#include <stdlib.h>
#include <string.h>
#include <hal/nrf_power.h>
#include <zephyr/fs/fcb.h>
#include <zephyr/kernel.h>
#include <zephyr/random/random.h>
#include <zephyr/storage/flash_map.h>

#include <mpsl_temp.h>

#include "lib/battery/battery.h"
#include "mic_health.h"

extern uint32_t transport_frame_no;  // transport.c: the codec's frames since boot, the audio's clock
extern uint32_t free_kb, dropped;    // lr_store.c
extern struct k_thread codec_thread, pusher_thread, store_thread_data, card_thread_data;
extern struct mic_health mic_health;  // codec.c
extern uint32_t pdm_errors;           // mic.c

#define N 160  // records the RAM batch holds: 2.5 KB
#define MAGIC 0x4C524C47
#define ID FIXED_PARTITION_ID(lr_log_partition)
// written to flash at once: what explains a reset must be there if the next one comes before the batch is full
#define URGENT (BIT(LR_BOOT) | BIT(LR_FAULT) | BIT(LR_MOUNT) | BIT(LR_APPEND_ERR) | BIT(LR_CARD_FULL) | BIT(LR_PAIR_FAIL) | \
                BIT(LR_POWER))  // the last minute before power is lost

struct rec {
    uint16_t boot;
    uint32_t t;
    uint8_t code, x;
    uint32_t a, b;
} __packed;
BUILD_ASSERT(sizeof(struct rec) == 16);

/* Records not in flash yet. __noinit: a crash or watchdog reset leaves them for the next boot to write; a cold start's
 * random RAM fails the check */
static __noinit struct {
    uint32_t magic, check;
    uint32_t first;  // seq of r[0]
    uint16_t boot, n;
    struct rec r[N];
} ram;

static struct fcb fcb;
static bool ready;
static struct k_spinlock lock;
static K_MUTEX_DEFINE(flush_mtx);
static struct k_thread *bt_rx;  // the connected callback runs on it: its stack is the one to watch

static uint32_t check(void)
{
    return MAGIC ^ ram.first ^ ram.boot ^ ram.n;
}

/* The batch so far as one FCB entry; the oldest sector goes when the log is full. */
static void flush(void)
{
    k_mutex_lock(&flush_mtx, K_FOREVER);
    uint16_t n = ram.n;  // records past n may arrive meanwhile: only lr_log writes there
    if (ready && n) {
        struct fcb_entry loc;
        uint16_t len = 4 + n * sizeof(struct rec);
        int err = fcb_append(&fcb, len, &loc);
        if (err == -ENOSPC) {
            err = fcb_rotate(&fcb) ?: fcb_append(&fcb, len, &loc);
        }
        if (!err) {
            off_t off = FCB_ENTRY_FA_DATA_OFF(loc);
            err = flash_area_write(fcb.fap, off, &ram.first, 4) ?:
                  flash_area_write(fcb.fap, off + 4, ram.r, len - 4) ?: fcb_append_finish(&fcb, &loc);
        }
        if (!err) {
            k_spinlock_key_t k = k_spin_lock(&lock);
            memmove(ram.r, ram.r + n, (ram.n - n) * sizeof(struct rec));
            ram.n -= n;
            ram.first += n;
            ram.check = check();
            k_spin_unlock(&lock, k);
        }
    }
    k_mutex_unlock(&flush_mtx);
}

static void flush_work_fn(struct k_work *w)
{
    flush();
}
static K_WORK_DEFINE(flush_work, flush_work_fn);

void lr_log(uint8_t code, uint8_t x, uint32_t a, uint32_t b)
{
    uint32_t f = transport_frame_no;
    if (code == LR_CONNECT) {
        bt_rx = k_current_get();
    }
    k_spinlock_key_t k = k_spin_lock(&lock);
    if (ram.n < N) {  // ponytail: a full batch drops the event; flushing at half full leaves 80 records of slack
        ram.r[ram.n++] = (struct rec){ram.boot, f ? f : k_uptime_get_32(), f ? code : code | LR_UPTIME, x, a, b};
        ram.check = check();
    }
    uint16_t n = ram.n;
    k_spin_unlock(&lock, k);
    if (n >= N / 2 || (code < 32 && BIT(code) & URGENT)) {  // a 32-bit mask: codes past 31 are not urgent
        k_work_submit(&flush_work);  // flash waits for radio slots: not on the caller's thread
    }
}

uint16_t lr_log_boot(void)
{
    return ram.boot;
}

void lr_log_sync(void)
{
    flush();
}

void lr_log_reset(uint8_t gpregret)
{
    lr_log(LR_DFU, gpregret, 0, 0);
    flush();
    NRF_POWER->GPREGRET = gpregret;
    NVIC_SystemReset();
}

static uint16_t battery_20mv(void)
{
    uint16_t mv = 0;
    battery_get_millivolt(&mv);
    return MIN(mv / 20, 255);
}

static uint16_t unused_stack(struct k_thread *t)
{
    size_t n;
    return t && t->stack_info.size && !k_thread_stack_space_get(t, &n) ? MIN(n, 0xFFFE) : 0xFFFF;
}

/* Every 5 minutes the batch goes to flash, every 10 a heartbeat (and the stacks, the mic's health) comes first */
static void tick(struct k_work *w)
{
    static uint32_t ticks;
    if (++ticks % 2 == 0) {
        lr_log(LR_HEARTBEAT, battery_20mv(), free_kb, dropped);
        lr_log(LR_STACKS, 0, (uint32_t) unused_stack(&codec_thread) << 16 | unused_stack(&pusher_thread),
               (uint32_t) unused_stack(&store_thread_data) << 16 | unused_stack(bt_rx));
        lr_log(LR_STACKS, 1, (uint32_t) unused_stack(&card_thread_data) << 16 | unused_stack(&k_sys_work_q.thread), 0);
        // the die temperature, in quarter degrees (MPSL owns the TEMP peripheral)
        lr_log(LR_HEALTH, (int8_t) (mpsl_temperature_get() / 4), mic_health_a(&mic_health, pdm_errors),
               mic_health_b(&mic_health));
        mic_health = (struct mic_health){0};
        pdm_errors = 0;
    }
    flush();
    k_work_reschedule(k_work_delayable_from_work(w), K_MINUTES(5));
}
static K_WORK_DELAYABLE_DEFINE(tick_work, tick);

int lr_log_init(void)
{
    static struct flash_sector sectors[4];
    uint32_t cnt = ARRAY_SIZE(sectors);
    const struct flash_area *fa;
    int err = flash_area_open(ID, &fa) ?: flash_area_get_sectors(ID, &cnt, sectors);
    if (!err) {
        fcb.f_magic = MAGIC;
        fcb.f_sectors = sectors;
        fcb.f_sector_cnt = cnt;
        // a log never written (the area held settings before, or a bad one): erased, started afresh
        if (fcb_init(ID, &fcb) || fcb_is_empty(&fcb)) {
            err = flash_area_erase(fa, 0, fa->fa_size) ?: fcb_init(ID, &fcb);
        }
    }
    ready = !err;

    // where flash ends: the last entry's first seq + its records, and the boot of its last record
    struct fcb_entry loc = {0}, last = {0};
    while (ready && !fcb_getnext(&fcb, &loc)) {
        last = loc;
    }
    uint32_t seq = 0;
    struct rec r = {0};
    if (last.fe_sector) {
        flash_area_read(fcb.fap, FCB_ENTRY_FA_DATA_OFF(last), &seq, 4);
        flash_area_read(fcb.fap, FCB_ENTRY_FA_DATA_OFF(last) + last.fe_data_len - sizeof(r), &r, sizeof(r));
        seq += (last.fe_data_len - 4) / sizeof(r);
    }
    // a batch that ends before the flash does is not this run's: a build places ram elsewhere, so a later one can find
    // an older run's batch, intact, where it left it; numbered on from there the log went back in seq and boot (10-07)
    if (ram.magic != MAGIC || ram.check != check() || ram.n > N || ram.first + ram.n < seq) {
        ram.magic = MAGIC;
        // a log started afresh (erased above) is numbered on from a random point below 2^31, not from 0 again: a reader
        // keys records by seq, and the old log's ones are already out there (a warm reset keeps the RAM's numbering)
        ram.first = last.fe_sector ? seq : sys_rand32_get() >> 1;
        ram.boot = r.boot;
        ram.n = 0;
    } else if (ram.first < seq) {  // a reset between writing a batch and taking it out of RAM: not twice
        uint16_t done = MIN(seq - ram.first, ram.n);
        memmove(ram.r, ram.r + done, (ram.n - done) * sizeof(struct rec));
        ram.n -= done;
        ram.first += done;
    }
    ram.check = check();
    flush();  // what the last boot left
    ram.boot++;
    ram.check = check();

    // the firmware: lr.sh's revision YYYYMMDD-<hash>[+] as the hash's 28 bits, bit 28 when built from a dirty tree
    const char *v = strchr(CONFIG_BT_DIS_FW_REV_STR, '-');
    char *end = NULL;
    uint32_t fw = v ? strtoul(v + 1, &end, 16) : 0;
    lr_log(LR_BOOT, battery_20mv(), NRF_POWER->RESETREAS, fw | (end && *end == '+') << 28);  // lr_store clears RESETREAS
    k_work_schedule(&tick_work, K_MINUTES(5));
    return err;
}

uint32_t lr_log_read(uint32_t from, uint8_t *buf, uint16_t max,
                     bool (*send)(uint32_t seq, uint16_t n, void *arg), void *arg)
{
    flush();
    struct fcb_entry loc = {0};
    bool ok = ready;
    while (ok && !fcb_getnext(&fcb, &loc)) {
        off_t off = FCB_ENTRY_FA_DATA_OFF(loc);
        uint32_t seq;
        uint16_t n = (loc.fe_data_len - 4) / sizeof(struct rec);
        flash_area_read(fcb.fap, off, &seq, 4);
        for (uint16_t i = from > seq ? MIN(from - seq, n) : 0; ok && i < n; i += max) {
            uint16_t k = MIN(max, n - i);
            ok = !flash_area_read(fcb.fap, off + 4 + i * sizeof(struct rec), buf, k * sizeof(struct rec)) &&
                 send(seq + i, k, arg);
        }
    }
    return ram.first + ram.n;
}
