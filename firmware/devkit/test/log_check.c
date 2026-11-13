// Host check of src/lr_log.c: a second of DROPPED gaps is one record (X1 10-18: some 2000 in a minute pushed the
// records before them out of the log), a new second or another event in between starts a new one, and a record already
// on its way to flash takes no more.
//   d=$(mktemp -d) && mkdir -p $d/zephyr/{fs,random,storage} $d/hal && for h in zephyr/fs/fcb zephyr/kernel \
//   zephyr/random/random zephyr/storage/flash_map hal/nrf_power mpsl_temp; do touch $d/$h.h; done && \
//   cc -w -I$d -o $d/log_check test/log_check.c && $d/log_check
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/types.h>

// --- the Zephyr API lr_log.c uses
#define CONFIG_BT_DIS_FW_REV_STR "20261105-abcdef0"
#define __packed __attribute__((packed))
#define __noinit
#define BUILD_ASSERT(...)
#define BIT(n) (1u << (n))
#define MIN(a, b) ((a) < (b) ? (a) : (b))
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define FIXED_PARTITION_ID(x) 0
#define K_MINUTES(x) (x)
#define K_FOREVER 0
#define K_MUTEX_DEFINE(name) struct k_mutex name
#define K_WORK_DEFINE(name, fn) struct k_work name
#define K_WORK_DELAYABLE_DEFINE(name, fn) struct k_work_delayable name
struct k_mutex { int x; };
struct k_work { int x; };
struct k_work_delayable { struct k_work work; };
struct k_spinlock { int x; };
typedef int k_spinlock_key_t;
struct k_thread { struct { size_t size; } stack_info; };
static struct { struct k_thread thread; } k_sys_work_q;
static k_spinlock_key_t k_spin_lock(struct k_spinlock *l) { return 0; }
static void k_spin_unlock(struct k_spinlock *l, k_spinlock_key_t k) {}
static int k_mutex_lock(struct k_mutex *m, int t) { return 0; }
static int k_mutex_unlock(struct k_mutex *m) { return 0; }
static int k_work_submit(struct k_work *w) { return 0; }
static int k_work_schedule(struct k_work_delayable *w, int t) { return 0; }
static int rescheduled;
static int k_work_reschedule(struct k_work_delayable *w, int t) { return ++rescheduled; }
static bool k_work_cancel_delayable(struct k_work_delayable *w) { return false; }
static struct k_work_delayable *k_work_delayable_from_work(struct k_work *w) { return (void *) w; }
static struct k_thread *k_current_get(void) { return 0; }
static uint32_t k_uptime_get_32(void) { return 0; }
static int k_thread_stack_space_get(struct k_thread *t, size_t *n) { return -1; }
static uint32_t sys_rand32_get(void) { return 0; }
static struct { uint32_t GPREGRET, RESETREAS; } nrf_power, *NRF_POWER = &nrf_power;
static void NVIC_SystemReset(void) {}
static int16_t mpsl_temperature_get(void) { return 0; }
int battery_get_millivolt(uint16_t *mv) { return 0; }
struct flash_sector { int x; };
struct flash_area { size_t fa_size; };
struct fcb_entry { void *fe_sector; uint16_t fe_data_len; off_t fe_elem_off; };
struct fcb { uint32_t f_magic; struct flash_sector *f_sectors; uint32_t f_sector_cnt; const struct flash_area *fap; };
#define FCB_ENTRY_FA_DATA_OFF(loc) ((loc).fe_elem_off)
static int fcb_append(struct fcb *f, uint16_t len, struct fcb_entry *loc) { loc->fe_elem_off = 0; return 0; }
static int fcb_rotate(struct fcb *f) { return 0; }
static int fcb_append_finish(struct fcb *f, struct fcb_entry *loc) { return 0; }
static int fcb_init(int id, struct fcb *f) { return 0; }
static int fcb_is_empty(struct fcb *f) { return 0; }
static int fcb_getnext(struct fcb *f, struct fcb_entry *loc) { return -ENOTSUP; }
static int flash_area_open(int id, const struct flash_area **fa) { return -ENOENT; }
static int flash_area_get_sectors(int id, uint32_t *cnt, struct flash_sector *s) { return 0; }
static int flash_area_erase(const struct flash_area *fa, off_t off, size_t len) { return 0; }
static int flash_area_read(const struct flash_area *fa, off_t off, void *buf, size_t len) { return 0; }
static uint8_t flash[4096];
static void (*while_writing)(void);  // what another thread logs while flush writes the batch
static int flash_area_write(const struct flash_area *fa, off_t off, const void *buf, size_t len)
{
    if (off && while_writing) {
        while_writing();  // flash waits for radio slots
    }
    memcpy(flash + off, buf, len);
    return 0;
}

uint32_t transport_frame_no, free_kb, dropped;
struct k_thread codec_thread, pusher_thread, store_thread_data, card_thread_data;
uint32_t pdm_errors;

#include "../src/lr_log.c"
struct mic_health mic_health;

static void drop(uint32_t frame, uint32_t n)  // lr_store_frame's: n frames lost before this one
{
    transport_frame_no = frame;
    dropped += n;
    lr_log(LR_DROPPED, 1, n, dropped);
}

static void dropped_while_writing(void) { drop(1170, 4); }  // the same second as the last record

int main(void)
{
    ram.n = 0, ram.boot = 3;
    // X1 10-18: 20-80 gaps a second of 1-3 frames each: one record a second of audio (frames are 10 ms)
    for (uint32_t f = 1000; f < 1100; f += 2) {
        drop(f, 1 + f % 3);
    }
    assert(ram.n == 1 && ram.r[0].code == LR_DROPPED && ram.r[0].t == 1000 && ram.r[0].x == 50);
    assert(ram.r[0].a == dropped && ram.r[0].b == dropped);
    drop(1100, 2);  // the next second
    assert(ram.n == 2 && ram.r[1].t == 1100 && ram.r[1].x == 1 && ram.r[1].a == 2 && ram.r[1].b == dropped);
    lr_log(LR_USB_DISK, 1, 0, 0);  // something else: the gaps after it are a record of their own, in order
    drop(1150, 3);
    assert(ram.n == 4 && ram.r[2].code == LR_USB_DISK && ram.r[3].code == LR_DROPPED && ram.r[3].a == 3);
    for (int i = 0; i < 300; i++) {  // x saturates
        drop(1160, 1);
    }
    assert(ram.n == 4 && ram.r[3].x == 255 && ram.r[3].a == 303 && ram.r[3].b == dropped);

    // written to flash while a gap comes: the record on its way stays as it was, the gap is a record after it
    ready = true, while_writing = dropped_while_writing;
    uint32_t first = ram.first;
    flush();
    struct rec out[4];
    memcpy(out, flash + 4, sizeof(out));
    assert(out[3].a == 303 && out[3].x == 255);
    assert(ram.first == first + 4 && ram.n == 1 && ram.r[0].code == LR_DROPPED && ram.r[0].a == 4 && ram.r[0].x == 1);
    while_writing = 0;
    drop(1180, 5);  // flushed: the batch's new last record takes gaps again
    assert(ram.n == 1 && ram.r[0].a == 9 && ram.r[0].x == 2 && ram.r[0].b == dropped);

    // parked (lr_log_stop): a tick already due neither logs (a heartbeat every 10 min) nor comes back
    tick(&tick_work.work);
    assert(rescheduled == 1);
    uint16_t n = ram.n;
    lr_log_stop();
    tick(&tick_work.work);  // the tenth minute: would be HEARTBEAT, STACKS x2, HEALTH
    assert(rescheduled == 1 && ram.n == n);
    printf("log_check ok\n");
    return 0;
}
