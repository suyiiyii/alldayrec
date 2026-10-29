// Host check of the record stream in src/lr_store.c: what a following phone gets (LIVE) and what the card gets are the
// same bytes at the same file and offset, through batches, 'T' records, frame gaps and a card that stops taking writes;
// the timing events (SD_WRITES, READ, READ_TIME) on a fake clock; LIVE behind a stalled link, a notify with no TX
// buffer and a link lost with records queued; phone mode (a phone that acks takes the stream instead of the card: in,
// a LIVE gap READ from RAM, the card off and on, out by ACK timeout, a full ring, the link, LIVE unsubscribed and a
// restart, a reset's RAM written at the next boot), no frame lost. -DPHONE=0: the other boards' store (no phone mode);
// -DCARD_OFF=0: phone mode with the card's supply left on.
//   d=$(mktemp -d) && mkdir -p $d/zephyr/{bluetooth,fs,sys,logging,drivers,random} && for h in bluetooth/conn \
//   bluetooth/gatt bluetooth/uuid fs/fs kernel logging/log sys/byteorder sys/ring_buffer drivers/sensor random/random; do
//   touch $d/zephyr/$h.h; done && for o in "" -DPHONE=0 -DCARD_OFF=0; do cc -O1 -w $o -I$d -o $d/store_check \
//   test/store_check.c && $d/store_check || break; done
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>

// --- the Zephyr API lr_store.c uses, as much as the stream needs
#define CONFIG_LR_STORE_RESERVE_KB 1024
#define CONFIG_LR_STORE_BATCH 4096
// phone mode, with the card's supply off: -DPHONE=0 builds the other boards' store, -DCARD_OFF=0 the card left on
#ifndef PHONE
#define PHONE 1
#endif
#ifndef CARD_OFF
#define CARD_OFF 1
#endif
#define CONFIG_LR_STORE_PHONE PHONE
#define CONFIG_LR_STORE_CARD_OFF CARD_OFF
#define IS_ENABLED(x) (x)
#define __DMB()
static unsigned int irq_lock(void) { return 0; }
static void irq_unlock(unsigned int k) {}
#define LOG_MODULE_REGISTER(...)
#define LOG_INF(...)
#define LOG_ERR(...)
#define __noinit
#define MIN(a, b) ((a) < (b) ? (a) : (b))
#define MAX(a, b) ((a) > (b) ? (a) : (b))
#define DIV_ROUND_UP(n, d) (((n) + (d) - 1) / (d))
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define K_FOREVER 0
#define K_NO_WAIT 0
#define K_MSEC(x) (x)
#define K_SECONDS(x) (x)
#define K_PRIO_PREEMPT(x) (x)
#define K_MSGQ_DEFINE(name, ...) static int name
#define K_WORK_DELAYABLE_DEFINE(name, fn) int name  // after "static"
#define K_THREAD_STACK_DEFINE(name, n) static char name[n]
#define K_THREAD_STACK_SIZEOF(s) sizeof(s)
#define BT_GATT_SERVICE_DEFINE(name, ...) static struct bt_gatt_attr name##_attrs[12]; static struct { struct bt_gatt_attr *attrs; } name = {name##_attrs}
#define BT_GATT_ERR(x) (-(x))
#define BT_ATT_ERR_INVALID_ATTRIBUTE_LEN 13
#define BT_GATT_CCC_NOTIFY 1
#define BT_ID_DEFAULT 0
#define BT_ADDR_LE_ANY ((const bt_addr_le_t *) 0)
#define BT_LE_CONN_PARAM(...) 0
#define BT_CONN_LE_PHY_PARAM_2M 0
#define FS_O_CREATE 1
#define FS_O_WRITE 2
#define FS_O_APPEND 4
#define FS_O_READ 8
#define FS_O_RDWR 10
#define FS_SEEK_SET 0
#define FS_DIR_ENTRY_FILE 0
typedef void (*k_thread_entry_t)(void *, void *, void *);
struct k_mutex { int x; };
struct sensor_value { int v; };
struct k_thread { int x; };
struct bt_conn { int x; };
struct bt_conn_info { struct { uint16_t interval; } le; };
struct k_work { int x; };
struct bt_gatt_attr { int x; };
typedef struct { uint8_t a; } bt_addr_le_t;
struct bt_bond_info { bt_addr_le_t addr; };
typedef struct { struct { uint32_t pc; } basic; } z_arch_esf_t;
struct fs_file_t { int f; uint32_t pos; };
struct fs_dir_t { int x; };
struct fs_dirent { int type; char name[16]; uint32_t size; };
struct fs_statvfs { unsigned long f_bfree, f_frsize, f_blocks; };
static struct { uint32_t RESETREAS; } power;
#define NRF_POWER (&power)
static void NVIC_SystemReset(void) {}
static void sys_put_le16(uint16_t v, uint8_t *b) { b[0] = v; b[1] = v >> 8; }
static void sys_put_le32(uint32_t v, uint8_t *b) { sys_put_le16(v, b); sys_put_le16(v >> 16, b + 2); }
static void sys_put_le64(uint64_t v, uint8_t *b) { sys_put_le32(v, b); sys_put_le32(v >> 32, b + 4); }
static uint16_t sys_get_le16(const uint8_t *b) { return b[0] | b[1] << 8; }
static uint32_t sys_get_le32(const uint8_t *b) { return sys_get_le16(b) | (uint32_t) sys_get_le16(b + 2) << 16; }
static uint64_t sys_get_le64(const uint8_t *b) { return sys_get_le32(b) | (uint64_t) sys_get_le32(b + 4) << 32; }
static bool card_held;  // someone holds the SD mutex for good (a card stuck in a write)
struct k_mutex write_sdcard_mutex;
static int sd_depth;      // the SD mutex held: the card writer waits for it (card_sem given meanwhile: card_waits)
static bool card_waits;
static void sem_given(int *s);
static int card_sem;
static int k_mutex_lock(struct k_mutex *m, int t)
{
    if (card_held) {
        return -EAGAIN;
    }
    sd_depth += m == &write_sdcard_mutex;
    return 0;
}
static int k_mutex_unlock(struct k_mutex *m)
{
    sd_depth -= m == &write_sdcard_mutex;
    if (!sd_depth && card_waits) {
        card_waits = false;
        sem_given(&card_sem);
    }
    return 0;
}
static int k_msgq_put(int *q, void *c, int t) { return 0; }
static int k_msgq_get(int *q, void *c, int t) { return -1; }
static int k_msgq_num_used_get(int *q) { return 0; }
static void k_sleep(int t) {}
static uint32_t sys_rand32_get(void) { return 0x1234; }
static int64_t now_ms;  // the clock: card writes, card reads and notifications take time
static int write_ms, read_ms, notify_ms;
static int64_t k_uptime_get(void) { return now_ms; }
static uint32_t k_uptime_get_32(void) { return (uint32_t) now_ms; }
static void k_work_cancel_delayable(int *w) {}
static void k_work_reschedule(int *w, int d) {}
static void k_thread_create(struct k_thread *t, char *s, size_t n, k_thread_entry_t e, void *a, void *b, void *c, int p, int o, int d) {}
static struct bt_conn the_conn;
static bool linked = true;  // a phone connected
struct bt_conn *get_current_connection(void) { return linked ? &the_conn : 0; }
static struct bt_conn *bt_conn_ref(struct bt_conn *c) { return c; }
static void bt_conn_unref(struct bt_conn *c) {}
static int bt_conn_le_param_update(struct bt_conn *c, int p) { return 0; }
static int bt_conn_le_phy_update(struct bt_conn *c, int p) { return 0; }
static int bt_conn_get_info(struct bt_conn *c, struct bt_conn_info *i) { i->le.interval = 12; return 0; }
static const bt_addr_le_t *bt_conn_get_dst(struct bt_conn *c) { return 0; }
static bool bt_addr_le_eq(const bt_addr_le_t *a, const bt_addr_le_t *b) { return true; }
static int bt_unpair(int id, const bt_addr_le_t *a) { return 0; }
static void bt_foreach_bond(int id, void (*f)(const struct bt_bond_info *, void *), void *arg) {}
static uint16_t bt_gatt_get_mtu(struct bt_conn *c) { return 185; }
// LIVE's queue and its sending thread: live_send() runs at each k_sem_give, unless the link is stalled
struct ring_buf { uint8_t *buf; uint32_t size, head, tail, claim; };  // read, committed, claimed: running counts
#define RING_BUF_DECLARE(name, n) static uint8_t name##_mem[n]; static struct ring_buf name = {name##_mem, n}
static uint32_t ring_buf_space_get(struct ring_buf *r) { return r->size - (r->claim - r->head); }
static uint32_t ring_buf_put_claim(struct ring_buf *r, uint8_t **d, uint32_t n)
{
    uint32_t at = r->claim % r->size;
    n = MIN(n, MIN(ring_buf_space_get(r), r->size - at));
    *d = r->buf + at;
    r->claim += n;
    return n;
}
static int ring_buf_put_finish(struct ring_buf *r, uint32_t n) { r->tail += n; assert(r->tail == r->claim); return 0; }
static uint32_t ring_buf_get_claim(struct ring_buf *r, uint8_t **d, uint32_t n)
{
    uint32_t at = r->head % r->size;
    *d = r->buf + at;
    return MIN(n, MIN(r->tail - r->head, r->size - at));
}
static int ring_buf_get_finish(struct ring_buf *r, uint32_t n) { r->head += n; return 0; }
static bool ring_buf_is_empty(struct ring_buf *r) { return r->head == r->tail; }
static uint32_t ring_buf_get(struct ring_buf *r, uint8_t *d, uint32_t n)
{
    n = MIN(n, r->tail - r->head);
    for (uint32_t i = 0; d && i < n; i++) {
        d[i] = r->buf[(r->head + i) % r->size];
    }
    r->head += n;
    return n;
}
#define __packed __attribute__((packed))
#define K_SEM_DEFINE(name, ...) int name##_unused  // after "static"; card_sem above, live_sem below
static int live_sem;
#define K_MUTEX_DEFINE(name) struct k_mutex name  // after "static"
#define K_THREAD_DEFINE(...)
static bool stalled;     // the link: nothing goes out (LIVE's thread waits on a TX buffer)
static bool card_stuck;  // the card: its writer sits in one write
// after lr_store.c: LIVE's thread or the card writer runs, unless held up
static void k_sem_give(int *s) { sem_given(s); }
static int k_sem_take(int *s, int t) { return 0; }
static bool holes_ok;  // the phone gets LIVE with a gap (it READs that from the card)
static bool subscribed = true;
static bool bt_gatt_is_subscribed(struct bt_conn *c, const struct bt_gatt_attr *a, int v) { return subscribed; }
static uint8_t status[64];  // TIME's read
static ssize_t bt_gatt_attr_read(struct bt_conn *c, const struct bt_gatt_attr *a, void *b, uint16_t l, uint16_t o, const void *v, uint16_t n) { memcpy(status, v, n); return n; }
int16_t usb_diag[4];
int lr_bond_count(void) { return 1; }
uint32_t lr_pause_left(void) { return 0; }
uint8_t lr_charge(void) { return 0; }
void lr_pause(uint32_t s) {}
static int reattached;  // the board left the USB bus and came back
void usb_reattach(void) { reattached++; }
volatile bool usb_disk_lent;
volatile uint32_t usb_disk_used;
bool usb_charge;
int mount_sd_card(void) { return 0; }
static int mounts, resets, mount_fails;  // tries of the card again (retry_card), from scratch; how many of the next fail
int mount_sd_card_once(void) { mounts++; return mount_fails ? mount_fails--, -EIO : 0; }
int mount_sd_card_reset(void) { resets++; return mount_fails ? mount_fails--, -EIO : 0; }
static int power_offs;  // the card's supply turned off (phone mode)
void sd_power_off(void) { power_offs++; }
static struct { uint8_t code, x; uint32_t a, b; } logged[16];
static int n_logged;
void lr_log(uint8_t code, uint8_t x, uint32_t a, uint32_t b)
{
    if (n_logged < 16) {
        logged[n_logged].code = code, logged[n_logged].x = x, logged[n_logged].a = a, logged[n_logged++].b = b;
    }
}
uint32_t lr_log_read(uint32_t from, uint8_t *buf, uint16_t max, bool (*send)(uint32_t, uint16_t, void *), void *arg) { return 0; }
void unmount_sd_card(void) {}

// a phone following the stream: file -> bytes by offset
#define FILES 48
static uint8_t phone[FILES][1 << 18];
static uint32_t phone_len[FILES];
static const struct bt_gatt_attr *data_attr, *ctrl_attr;  // READ's: DATA offset:u32 bytes..., CTRL replies
static uint32_t data_next;                                // the offset the next DATA notification must have
static int data_into = -1;                                // READ's DATA into the phone's copy of this segment too
static int live_nomem;     // LIVE notifies refused for want of a TX buffer (-ENOMEM) before one goes
static int live_gone = -1; // LIVE notifies that go before the link is lost (-ENOTCONN from then on); -1: never
static int bt_gatt_notify(struct bt_conn *c, const struct bt_gatt_attr *a, const void *data, uint16_t len)
{
    const uint8_t *p = data;
    assert(len <= bt_gatt_get_mtu(c) - 3);
    if (a == ctrl_attr) {
        return 0;
    }
    if (a == data_attr) {
        assert(sys_get_le32(p) == data_next);
        if (data_into >= 0) {
            memcpy(phone[data_into] + data_next, p + 4, len - 4);
            phone_len[data_into] = MAX(phone_len[data_into], data_next + len - 4);
        }
        data_next += len - 4;
        now_ms += notify_ms;
        return 0;
    }
    if (live_nomem) {
        live_nomem--;
        return -ENOMEM;
    }
    if (!live_gone) {
        return -ENOTCONN;
    }
    live_gone -= live_gone > 0;
    uint32_t file = sys_get_le32(p) & 0xffff, off = sys_get_le32(p + 4);
    assert(file < FILES);
    assert(off == phone_len[file] || (holes_ok && off > phone_len[file]));  // no holes while the phone follows
    memcpy(phone[file] + off, p + 8, len - 8);
    phone_len[file] = off + len - 8;
    return 0;
}

// the card: /SD:/LR/0001SSSS.BIN -> bytes; BOOT
static uint8_t card[FILES][1 << 18];
static uint32_t card_len[FILES];
static int card_fail;  // writes fail with this
static int seg_of(const char *path) { return strstr(path, ".BIN") ? (int) (strtoul(strstr(path, ".BIN") - 4, 0, 16)) : -1; }
#define BOOT_F (-2)  // /SD:/LR/BOOT, the card's boot count
static uint8_t boot_file[2];
static uint32_t boot_len;
static int boot_fail;  // BOOT's writes fail with this
static int made;  // on the card, made by fs_mkdir/fs_open since it was wiped: 1 /LR, 2 .metadata_never_index, 4 .fseventsd/no_log
static int fs_open(struct fs_file_t *f, const char *path, int flags)
{
    if (strstr(path, "/LR/") && !(made & 1)) {
        return -ENOENT;
    }
    made |= strstr(path, "never_index") ? 2 : strstr(path, "no_log") ? 4 : 0;
    f->f = strstr(path, "/BOOT") ? BOOT_F : seg_of(path);
    f->pos = 0;
    return 0;
}
static int sync_fail, close_fail;  // fs_sync and fs_close fail with these: the file's length not on the card
static int fs_close(struct fs_file_t *f) { return close_fail; }
static ssize_t fs_write(struct fs_file_t *f, const void *d, size_t n)
{
    if (f->f == BOOT_F) {
        if (boot_fail) {
            return boot_fail;
        }
        memcpy(boot_file + f->pos, d, n);
        boot_len = MAX(boot_len, f->pos + n);
        return n;
    }
    if (card_fail) {
        return card_fail;
    }
    if (f->f >= 0) {
        memcpy(card[f->f] + card_len[f->f], d, n);
        card_len[f->f] += n;
    }
    now_ms += write_ms;
    return n;
}
static ssize_t fs_read(struct fs_file_t *f, void *d, size_t n)
{
    n = MIN(n, (f->f == BOOT_F ? boot_len : card_len[f->f]) - f->pos);
    memcpy(d, (f->f == BOOT_F ? boot_file : card[f->f]) + f->pos, n);
    f->pos += n;
    now_ms += read_ms;
    return n;
}
static int fs_seek(struct fs_file_t *f, long o, int w) { f->pos = o; return 0; }
static int fs_sync(struct fs_file_t *f) { return sync_fail; }
static void fs_file_t_init(struct fs_file_t *f) {}
static void fs_dir_t_init(struct fs_dir_t *d) {}
static const char *dir[4];  // /LR's files, for the boot count (none: no directory)
static int dir_n, dir_at;
static int fs_opendir(struct fs_dir_t *d, const char *p) { return dir_n ? (dir_at = 0) : -ENOENT; }
static int fs_readdir(struct fs_dir_t *d, struct fs_dirent *e)
{
    e->type = FS_DIR_ENTRY_FILE;
    strcpy(e->name, dir_at < dir_n ? dir[dir_at++] : "");
    return 0;
}
static int fs_closedir(struct fs_dir_t *d) { return 0; }
static int fs_mkdir(const char *p) { made |= !strcmp(p, "/SD:/LR"); return 0; }
static int fs_unlink(const char *p) { return 0; }
static int fs_statvfs(const char *p, struct fs_statvfs *v) { v->f_bfree = v->f_blocks = 1 << 20; v->f_frsize = 4096; return 0; }

static uint16_t chip_boot;  // lr_log's own count
uint16_t lr_log_boot(void) { return chip_boot; }
uint32_t transport_frame_no;
uint32_t lr_sd_err[5];  // sdcard.h: the SD driver's failed tries
#include "../src/lr_store.c"

static void tick(void)  // the card writer's turn: a batch, an ACK, or its second
{
    k_mutex_lock(&write_sdcard_mutex, K_FOREVER);
    card_drain();
    phone_step();
    k_mutex_unlock(&write_sdcard_mutex);
}

static void sem_given(int *s)
{
    if (s == &live_sem && !stalled) {
        live_send();
    } else if (s == &card_sem && sd_depth) {
        card_waits = true;
    } else if (s == &card_sem && !card_stuck) {
        tick();
    }
}

static int append1(void)  // a one-byte batch through the ring, as the writer appends one
{
    struct card_rec h = {0, 0, 1};
    ring_copy(q.head, &h, sizeof(h), true);
    ring_copy(q.head + sizeof(h), &(uint8_t){0}, 1, true);
    q.head = (q.head + sizeof(h) + 1) % (2 * RING);
    int err = append(1);
    ring_drop(sizeof(h) + 1);
    return err;
}

static void frames(uint32_t from, uint32_t n)
{
    for (uint32_t i = from; i < from + n; i++) {
        uint8_t opus[40];
        memset(opus, (uint8_t) i, sizeof(opus));
        lr_store_frame(i, opus, sizeof(opus) - i % 7, false);  // VBR: sizes vary
    }
}

/* The phone's ACK, as ctrl_write takes it: the card writer looks at once */
static void ack(uint32_t file, uint32_t off)
{
    uint8_t b[9] = {ACK};
    sys_put_le32(file, b + 1);
    sys_put_le32(off, b + 5);
    assert(ctrl_write(&the_conn, CTRL_ATTR, b, sizeof(b), 0, 0) == sizeof(b));
}

/* n seconds of audio, the card writer's tick each; the phone acks what it has every 5th (acks: 1) */
static void seconds(int n, bool acks)
{
    for (int i = 0; i < n; i++) {
        frames(rec_next, 100);
        now_ms += 1000;
        if (acks && i % 5 == 4) {
            ack(cur_file(), phone_len[seg]);
        } else {
            tick();
        }
    }
}

/* The frames in a file's records, marked in seen[frame - base] */
static uint8_t seen[1 << 16];
static void mark(const uint8_t *b, uint32_t n, uint32_t base)
{
    for (uint32_t i = 0; i + 6 <= n;) {
        if (b[i] == 'T') {
            i += 13;
            continue;
        }
        uint32_t f = sys_get_le32(b + i + 2);
        int c = b[i + 1];
        i += 6;
        while (c-- && i < n) {
            if (f >= base && f - base < sizeof(seen)) {
                seen[f - base] = 1;
            }
            f++;
            i += 1 + b[i];
        }
    }
}

/* The phone READs the open file from `from` (what LIVE left out), then acks its copy: phone mode */
static void catch_up(uint32_t from)
{
    data_attr = DATA_ATTR, ctrl_attr = CTRL_ATTR, data_into = seg, data_next = from;
    send_file(&the_conn, cur_file(), from);
    data_into = -1;
    ack(cur_file(), phone_len[seg]);
    assert(phone_mode);
}

static int last_code(uint8_t code)  // the last such event logged, its index; -1: none
{
    for (int i = n_logged - 1; i >= 0; i--) {
        if (logged[i].code == code) {
            return i;
        }
    }
    return -1;
}

/* Parse a file's records: frames and 'T's, consecutive within each 'A', nothing left over. */
static void parse(const uint8_t *b, uint32_t n, int *a_frames, int *syncs)
{
    for (uint32_t i = 0; i < n;) {
        if (b[i] == 'A') {
            int c = b[i + 1];
            assert(c >= 1 && c <= REC_FRAMES);
            i += 6;
            while (c--) {
                i += 1 + b[i];
                ++*a_frames;
            }
        } else {
            assert(b[i] == 'T');
            i += 13;
            ++*syncs;
        }
        assert(i <= n);
    }
}

int main(void)
{
    assert(lr_store_init() == 0 && boot == BOOT_FLOOR && store_err == 0);  // a new card, the chip's first boot

    frames(0, 950);       // 9.5 s: several batches
    put_sync(950, 123);   // a phone's TIME
    frames(950, 50);
    frames(1100, 45);     // 100 frames lost on the way (a gap): a new record
    write_out();          // a LIST or READ mid-record: what is finished goes to the card, the open record stays
    assert(card_len[0] == phone_len[0] && rec_count == 5);
    frames(1145, 55);
    // the card trails the stream by less than a batch; READ (or a reset) writes the rest
    assert(card_len[0] < phone_len[0] && phone_len[0] - card_len[0] < BATCH + 6 + REC_FRAMES * 256);
    assert(memcmp(card[0], phone[0], card_len[0]) == 0);
    flush();
    assert(card_len[0] == phone_len[0] && memcmp(card[0], phone[0], card_len[0]) == 0 && seg_size == card_len[0]);
    int a = 0, t = 0;
    parse(card[0], card_len[0], &a, &t);
    assert(a == 1100 && t == 1);

    // the card stops taking writes: the stream goes on in the same file, the card's copy stays its start
    uint32_t before = card_len[0];
    card_fail = -EIO;
    frames(1200, 300);
    card_fail = 0;
    frames(1500, 200);
    flush();
    assert(store_err == -EIO && card_len[0] == before && phone_len[0] > before && seg == 0);
    assert(memcmp(card[0], phone[0], card_len[0]) == 0);

    // the card tried again (once a minute): back, in a new segment, the card and the stream the same again
    // a card that failed is mounted again every 5 s at first (one that comes back by itself, BDAE 10-08 14:45), after 6
    // failed tries reset from scratch once a minute (one stuck, 11:29), after 3 of those every 10 minutes (dead, or
    // gone); back, every 5 s again for the next failure. None at boot: once a minute
    boot = 0;
    assert(retry_every() == 60000);
    boot = 1;
    assert(retry_every() == 5000);
    mount_fails = 9;
    for (int i = 0; i < 6; i++) {
        retry_card();
    }
    assert(mounts == 6 && resets == 0 && store_err == -EIO && retries == 6 && retry_every() == 60000);
    for (int i = 0; i < 3; i++) {
        retry_card();
    }
    assert(mounts == 6 && resets == 3 && retries == 9 && retry_every() == 600000);
    retry_card();  // the reset brings it back
    assert(mounts == 6 && resets == 4 && store_err == 0);
    assert(retries == 10 && retry_every() == 5000);  // looked at every 5 s; counted on until it takes a write
    mounts = 0;
    frames(1700, 500);
    flush();
    assert(retries == 0);
    assert(seg == 1 && card_len[1] == phone_len[1] && memcmp(card[1], phone[1], card_len[1]) == 0);
    a = t = 0;
    parse(phone[0], phone_len[0], &a, &t);
    parse(phone[1], phone_len[1], &a, &t);
    assert(a == 2100 && t == 1);  // every frame in the stream, 1200..1699 too though the card missed them

    // lent to USB: the stream goes on, the card does not; back, a new segment that is the stream's from its start
    store_err = -EBUSY;
    frames(2200, 343);  // ends with finished records still in RAM (not a whole batch)
    store_err = 0;
    assert(done > 0);
    restart_segment();  // as lend(false) does
    frames(2543, 290);
    flush();
    assert(seg == 2 && card_len[2] == phone_len[2] && memcmp(card[2], phone[2], card_len[2]) == 0);

    // the link stalls: LIVE's queue fills and the records after it are left out of LIVE, not out of the card;
    // once one goes again, LIVE_LOST says how many; the phone READs the gap
    n_logged = 0;
    stalled = true;
    uint32_t from = seg_size;
    int a0 = 0, s0 = 0;
    parse(card[2], from, &a0, &s0);  // the segment so far
    frames(2833, 400);  // 4 s: more than the queue holds
    assert(live_lost > 10 && live_lost_bytes > 2000 && phone_len[2] < from + 1);
    uint32_t lost = live_lost, lost_bytes = live_lost_bytes;
    stalled = false;
    holes_ok = true;
    live_send();  // the link is back: what the queue holds goes out
    frames(3233, 100);
    flush();
    assert(n_logged == 1 && logged[0].code == LR_LIVE_LOST && logged[0].a == lost && logged[0].b == lost_bytes);
    assert(live_lost == 0 && card_len[2] == phone_len[2] && seg == 2);  // the card has it all, the phone up to the end
    assert(memcmp(card[2], phone[2], from) == 0);
    a = t = 0;
    parse(card[2], card_len[2], &a, &t);
    assert(a == a0 + 400 + 100);  // every frame on the card
    holes_ok = false;

    // no TX buffer for a while (-ENOMEM): LIVE waits for one, nothing is left out, nothing logged
    n_logged = 0;
    uint32_t at = seg_size;
    live_nomem = 25;
    frames(3333, 50);
    flush();
    assert(live_nomem == 0 && n_logged == 0 && card_len[2] == phone_len[2]);
    assert(memcmp(card[2] + at, phone[2] + at, card_len[2] - at) == 0);

    // the link lost with records queued: the one it cut short and those after it go out of LIVE, counted once the
    // queue is empty (LIVE_LOST x 1, a record cut short counts whole); the card has them all, the phone sees a hole
    at = seg_size;
    stalled = true;     // three records wait in LIVE's queue
    frames(3383, 31);
    uint32_t queued = seg_size - at;
    stalled = false;
    live_gone = 1;      // one notify goes, then the link is gone
    live_send();
    assert(n_logged == 1 && logged[0].code == LR_LIVE_LOST && logged[0].x == 1);
    assert(logged[0].a == 3 && logged[0].b == queued && phone_len[2] < at + queued);
    live_gone = -1;     // the phone is back
    holes_ok = true;
    frames(3414, 19);
    flush();
    assert(n_logged == 1 && card_len[2] == phone_len[2]);  // LIVE goes on past the hole, the same bytes as the card
    assert(memcmp(card[2] + at + queued, phone[2] + at + queued, card_len[2] - at - queued) == 0);
    a = t = 0;
    parse(card[2], card_len[2], &a, &t);
    assert(a == a0 + 400 + 100 + 50 + 31 + 19);  // every frame on the card
    holes_ok = false;

    // nobody follows: the card alone, same records
    subscribed = false;
    frames(3433, 100);
    flush();
    assert(card_len[2] > phone_len[2]);

    // the card stalls: the stream does not wait for it. LIVE gets every record; the card the batches its queue
    // kept, then nothing more of that segment (APPEND_ERR x 1, -EAGAIN); once the writer has caught up, the stream goes
    // on in a new segment, which the card has from its start
    subscribed = true;
    next_segment();
    n_logged = 0;
    uint16_t sg = seg;
    uint32_t dropped0 = dropped;
    card_stuck = true;
    frames(3533, 2000);  // 20 s: more than the queue holds
    assert(seg == sg && card_behind && card_len[sg] == 0 && dropped == dropped0);
    assert(n_logged == 1 && logged[0].code == LR_APPEND_ERR && logged[0].x == 1 && (int32_t) logged[0].a == -EAGAIN);
    card_stuck = false;
    card_drain();  // the write returns: the queue goes onto the card
    uint32_t kept = card_len[sg];
    assert(kept >= RING / 2 && kept < phone_len[sg] && memcmp(card[sg], phone[sg], kept) == 0);
    frames(5533, 500);
    flush();
    assert(seg == sg + 1 && card_len[sg] == kept && !card_behind);
    assert(card_len[seg] == phone_len[seg] && memcmp(card[seg], phone[seg], card_len[seg]) == 0);
    a = t = 0;
    parse(phone[sg], phone_len[sg], &a, &t);
    parse(phone[seg], phone_len[seg], &a, &t);
    assert(a == 2500 && t == 0);  // every frame went out by LIVE

    // card appends over 10 minutes, one SD_WRITES: x over 200 ms, a total | slowest << 20, b <20 | 20-49 << 10 | 50-200 << 20
    memset(&wr, 0, sizeof(wr));
    wr.start = now_ms = 1000;
    n_logged = 0;
    int took[] = {5, 5, 19, 20, 49, 50, 200, 250, 1001};
    for (int i = 0; i < ARRAY_SIZE(took); i++) {
        write_ms = took[i];
        assert(append1() == 0);
    }
    assert(n_logged == 1 && logged[0].code == LR_SLOW && logged[0].a == 1001);  // over 1000 ms only
    now_ms = 1000 + 10 * 60 * 1000;
    write_ms = 3;
    assert(append1() == 0);
    assert(n_logged == 2 && logged[1].code == LR_SD_WRITES && logged[1].x == 2);
    assert(logged[1].a == (1602 | 1001 << 20) && logged[1].b == (4 | 2 << 10 | 2 << 20));
    assert(wr.start == now_ms && wr.ms == 0);
    // the driver's failed tries, when there were any: with the next SD_WRITES, then cleared
    lr_sd_err[LR_SD_CRC] = 2, lr_sd_err[LR_SD_TIMEOUT] = 70000, lr_sd_err[LR_SD_BUSY] = 3, lr_sd_err[LR_SD_RETRIED] = 300;
    now_ms += 10 * 60 * 1000;
    assert(append1() == 0);
    assert(n_logged == 4 && logged[2].code == LR_SD_WRITES && logged[3].code == LR_SD_ERRORS && logged[3].x == 255);
    assert(logged[3].a == (2 | 0xFFFF << 16) && logged[3].b == 3 && !lr_sd_err[LR_SD_CRC] && !lr_sd_err[LR_SD_RETRIED]);
    // and at an append that fails: no SD_WRITES may come after it
    lr_sd_err[LR_SD_OTHER] = 1;
    card_fail = -EIO;
    assert(append1() == -EIO);
    card_fail = 0;
    assert(n_logged == 6 && logged[4].code == LR_APPEND_ERR && logged[5].code == LR_SD_ERRORS && logged[5].b == 1 << 16);
    assert(append1() == 0);  // the run of failures is over
    write_ms = 0;

    // a READ of a finished file from offset 100: READ (interval, bytes, ms | payload << 22) and READ_TIME (card, notify)
    data_attr = DATA_ATTR;
    ctrl_attr = CTRL_ATTR;
    n_logged = 0;
    read_ms = 7, notify_ms = 2;
    data_next = 100;
    int64_t t0 = now_ms;
    send_file(&the_conn, 1 << 16, 100);
    uint32_t bytes = card_len[0] - 100, payload = 185 - 3 - 4;
    uint32_t reads = (bytes + CHUNK - 1) / CHUNK + 1, sends = 0;  // the last read finds the end
    for (uint32_t off = 0; off < bytes; off += CHUNK) {
        sends += (MIN(CHUNK, bytes - off) + payload - 1) / payload;
    }
    assert(data_next == card_len[0] && n_logged == 2);
    assert(logged[0].code == LR_READ && logged[0].x == 12 && logged[0].a == bytes);
    assert(logged[0].b == ((now_ms - t0) | payload << 22) && now_ms - t0 == reads * 7 + sends * 2);
    assert(logged[1].code == LR_READ_TIME && logged[1].a == reads * 7 && logged[1].b == sends * 2);
    send_file(&the_conn, 1 << 16, card_len[0]);  // nothing to send: no event
    assert(n_logged == 2);
    // a reset while the card is stuck in a write: the shutdown gives up on the SD mutex and says so, the reset goes on
    card_held = true;
    n_logged = 0;
    lr_store_shutdown();
    assert(n_logged == 1 && logged[0].code == LR_APPEND_ERR && logged[0].x == 2 && (int32_t) logged[0].a == -EDEADLK);
    card_held = false;

    // the boot count: never one used before, whatever the card says
    uint16_t c;
    n_logged = 0, boot_len = 0, chip_boot = 7;  // a new card (no BOOT yet), the chip's 7th boot
    assert(card_init(mount_sd_card, &c) == 0 && c == 7 + BOOT_FLOOR && sys_get_le16(boot_file) == c);
    assert(logged[0].code == LR_STORE && logged[0].x == 1 && logged[0].a == c);  // BOOT did not read back
    chip_boot = 8;  // the next boot: card and chip agree
    assert(card_init(mount_sd_card, &c) == 0 && c == 8 + BOOT_FLOOR && logged[1].x == 0);
    sys_put_le16(22, boot_file), chip_boot = 116;  // a card counted the old way (10-13: 22)
    assert(card_init(mount_sd_card, &c) == 0 && c == 116 + BOOT_FLOOR);
    boot_len = 0, dir[0] = "10800003.BIN", dir[1] = "BOOT", dir_n = 2;  // BOOT lost, a newer file there
    assert(card_init(mount_sd_card, &c) == 0 && c == 0x1081 && logged[3].x == 1);
    boot_len = 0, boot_fail = -EIO;  // a count that neither reads nor writes: said, and the boot goes on
    assert(card_init(mount_sd_card, &c) == 0 && c == 0x1081 && logged[4].x == (1 | 2));
    boot_fail = 0;
    // a card falling apart (BDAE 10-13: /LR all garbage): names not ours left out, counts far past the chip's too
    dir[0] = "12G4ABCD.BIN", dir[1] = "10900003.BI~", dir[2] = "1090000.BIN", dir_n = 3;  // ~ 4240 if read as hex
    boot_len = 0;
    assert(card_init(mount_sd_card, &c) == 0 && c == 116 + BOOT_FLOOR && logged[5].x == 1);
    dir[0] = "FFFF1234.BIN", dir_n = 1;  // hex, but no boot ever got that far: left out, and said
    boot_len = 0;
    assert(card_init(mount_sd_card, &c) == 0 && c == 116 + BOOT_FLOOR && logged[6].x == (1 | 4));
    dir_n = 0, sys_put_le16(0xFFFF, boot_file);  // BOOT garbage: no wrap to 0
    assert(card_init(mount_sd_card, &c) == 0 && c == 116 + BOOT_FLOOR && logged[7].x == 4);
    dir_n = 1, sys_put_le16(0xFFFF, boot_file);  // both
    assert(card_init(mount_sd_card, &c) == 0 && c == 116 + BOOT_FLOOR && logged[8].x == 4);
    dir_n = 0;
    // the file's length that does not go to the card with its bytes (fs_sync fails: a READ would end short of them, BDAE
    // 10-13) fails the write: said (APPEND_ERR x 3), the rest left out, the card tried again, the stream on in a new file
    {
        assert(store_err == 0);
        uint16_t s0 = seg;
        uint32_t had = card_len[s0];
        assert(s0 + 3 < FILES);
        n_logged = 0, sync_fail = -EIO;
        frames(rec_next, 300);
        sync_fail = 0;
        flush();
        assert(store_err == -EIO && n_logged == 1 && logged[0].code == LR_APPEND_ERR && logged[0].x == 3);
        assert((int32_t) logged[0].a == -EIO && card_len[s0] == had + logged[0].b);  // the first batch, then nothing
        retry_card();
        assert(store_err == 0 && seg == s0 + 1);
        // a close that fails at a file's end, the same: said with the file's bytes, the card tried again
        frames(rec_next, 300);
        flush();
        uint32_t len = card_len[seg];
        assert(len && card_at == len);
        next_segment();
        n_logged = 0, close_fail = -EIO;
        frames(rec_next, 300);
        flush();  // the new file's first batch closes the old one
        close_fail = 0;
        assert(store_err == -EIO && n_logged == 1 && logged[0].code == LR_APPEND_ERR && logged[0].x == 3 && logged[0].b == len);
        assert(card_len[seg] == 0);  // nothing of the new file until the card is back
        retry_card();
        assert(store_err == 0 && seg == s0 + 3);
    }

    // a card that reads but does not write (BDAE 10-11, the injector's "busy after a write"): every try mounts it,
    // its next write fails again. That is no card back: the tries count on, 6 quick mounts, then resets from scratch
    // once a minute 3 times, then every 10 minutes. Mounted, it is looked at every 5 s; once a write goes, quick again
    {
        mounts = resets = mount_fails = 0;
        frames(rec_next, 50);
        flush();
        assert(store_err == 0 && retries == 0);
        card_fail = -EIO;
        frames(rec_next, 50);
        flush();
        assert(store_err == -EIO);
        int64_t every[10], want[10] = {5000, 5000, 5000, 5000, 5000, 5000, 60000, 60000, 60000, 600000};
        for (int i = 0; i < 10; i++) {
            every[i] = retry_every();
            retry_card();
            assert(store_err == 0 && retry_every() == 5000);  // mounted: reads work
            frames(rec_next, 50);
            flush();
            assert(store_err == -EIO);  // the first write fails
            assert(mounts == MIN(i + 1, 6) && resets == MAX(i - 5, 0));
        }
        assert(retries == 10 && memcmp(every, want, sizeof(want)) == 0 && retry_every() == 600000);
        card_fail = 0;  // the reset wakes it up
        retry_card();
        assert(store_err == 0 && resets == 5 && retries == 11);
        frames(rec_next, 50);
        flush();
        assert(store_err == 0 && retries == 0 && retry_every() == 5000 && card_len[seg] == phone_len[seg]);
    }

    // none at boot (BDAE 10-14: one put in later did not mount by plain tries): from scratch from the first try, once a
    // minute 3 times, then every 10 minutes; back, it counts this boot then and starts at its segment 0
    boot = 0, store_err = -ETIMEDOUT, retries = 0, mounts = resets = 0, mount_fails = 4, n_logged = 0;
    assert(retry_every() == 60000);
    for (int i = 0; i < 3; i++) {
        retry_card();
    }
    assert(retry_every() == 600000);
    retry_card();
    assert(mounts == 0 && resets == 4 && store_err == -ETIMEDOUT && boot == 0 && n_logged == 0);
    uint16_t counted = sys_get_le16(boot_file);
    retry_card();
    assert(mounts == 0 && resets == 5 && store_err == 0 && boot == counted + 1 && seg == 0 && retries == 5 && retry_every() == 5000);
    assert(n_logged == 2 && logged[0].code == LR_STORE && logged[1].code == LR_SEGMENT && logged[1].a == (uint32_t) boot << 16);

    // lent to a computer that then leaves it alone (ejected: macOS's eject never reaches the disk driver; forgotten; never
    // mounted) for 3 minutes: back by itself; read now and then, it stays lent. Looked at every 5 s while lent
    usb_charge = true, retries = 9, n_logged = 0, now_ms = 0xFFFF0000;  // near the 32-bit clock's wrap
    usb_disk_used = 0x80000000;  // the last lend's reads, long ago
    reattached = 0;
    assert(lend(true, BACK_PHONE) == 0 && usb_disk_lent && store_err == -EBUSY && retry_every() == 5000);
    assert(reattached == 1);  // a computer that ejected the card before looks again
    now_ms += LEND_IDLE_MS - 1;  // left alone from the lend
    lend_if_idle();
    assert(usb_disk_lent);
    usb_disk_used = k_uptime_get_32();  // a read
    now_ms += LEND_IDLE_MS - 1;
    lend_if_idle();
    assert(usb_disk_lent && store_err == -EBUSY);
    now_ms += 1;
    lend_if_idle();
    assert(!usb_disk_lent && store_err == 0 && n_logged >= 2 && logged[0].code == LR_USB_DISK && logged[0].x == 1);
    assert(logged[n_logged - 1].code == LR_USB_DISK && logged[n_logged - 1].x == 0 && logged[n_logged - 1].b == BACK_IDLE);
    assert(reattached == 2);  // back: a computer that has it mounted lets go of it (X1 10-17: macOS 26 hung on it)
    lend_if_idle();  // back: nothing more
    assert(logged[n_logged - 1].code == LR_USB_DISK && logged[n_logged - 1].b == BACK_IDLE && !usb_disk_lent);
    assert(reattached == 2);
    // taken back by the phone: off the bus and back, as above; unplugged: off the bus already
    assert(lend(true, BACK_PHONE) == 0 && lend(false, BACK_PHONE) == 0 && !usb_disk_lent && store_err == 0 && reattached == 4);
    assert(lend(true, BACK_PHONE) == 0 && reattached == 5);
    usb_charge = false;
    assert(lend(false, BACK_UNPLUGGED) == 0 && !usb_disk_lent && store_err == 0 && reattached == 5);
    assert(logged[n_logged - 1].code == LR_USB_DISK && logged[n_logged - 1].x == 0 && logged[n_logged - 1].b == BACK_UNPLUGGED);
    usb_charge = true;  // on the computer again for what follows

    // wiped on the computer it was lent to (X1 10-17: erased as exFAT on the Mac), or a blank card put in after a failed
    // write: it mounts with no /LR, its writes failed -ENOENT for good. Mounted, it gets its /LR and the hidden
    // files again and takes the next write
    memset(phone_len, 0, sizeof(phone_len)), memset(card_len, 0, sizeof(card_len));  // the card-less boot's: from segment 0
    for (int i = 0; i < 2; i++) {
        if (i == 0) {
            assert(lend(true, BACK_PHONE) == 0);
            made = 0;
            lend(false, BACK_IDLE);
        } else {
            card_fail = -EIO;
            frames(rec_next, 50);
            flush();
            assert(store_err == -EIO);
            card_fail = 0, made = 0;
            retry_card();
        }
        assert(store_err == 0);
        frames(rec_next, 50);
        flush();
        assert(store_err == 0 && made == 7 && card_len[seg] == phone_len[seg]);
    }
    // phone mode (LR-P3): a phone that acks what it has stored takes the stream, the card is left alone and, idle,
    // turned off; what the phone has not acked stays in RAM, and goes to the card in a new file when the phone goes,
    // stops acking, or falls too far behind. Every frame is in the phone's acked copy or on the card. An app that never
    // acks (all of the above) never started it
    time_read(&the_conn, 0, 0, 0, 0);
    assert(status[48] == PHONE && !phone_mode && last_code(LR_PHONE) < 0);
    if (PHONE) {
        memset(phone_len, 0, sizeof(phone_len)), memset(card_len, 0, sizeof(card_len));
        boot = 2, seg = 0, seg_size = 0, card_file = UINT32_MAX, seg_open = false;  // a boot's files from segment 0
        subscribed = linked = true, holes_ok = false, n_logged = 0;
        uint32_t f0 = rec_next, s0 = seg;  // every frame from here on is in the phone's acked copy or on the card
        seconds(3, false);
        assert(!phone_mode && card_len[s0] > 0);
        ack(cur_file(), phone_len[s0]);  // the first ACK
        assert(phone_mode && n_logged == 1 && logged[0].code == LR_PHONE && logged[0].x == 0 && logged[0].a == 0);
        uint32_t on_card = card_len[s0];
        seconds(4, false);
        assert(card_len[s0] == on_card && ring_used() > 4 * 3500);  // in RAM, not on the card
        ack(cur_file(), phone_len[s0]);
        assert(ring_used() == 0);  // acked: out of RAM
        seconds(7, true);  // idle 10 s: the card's supply off
        assert(phone_mode && card_off == CARD_OFF && power_offs == CARD_OFF && card_len[s0] == on_card);

        // LIVE left records out (a stalled link): the phone READs them, from RAM; the card stays off
        int resets0 = resets;
        uint32_t hole = phone_len[s0];
        stalled = true;
        frames(rec_next, 300);
        assert(live_lost > 0);
        stalled = false, holes_ok = true;
        live_send();
        frames(rec_next, 20);
        uint32_t end = seg_size;
        catch_up(hole);
        assert(data_next >= end && ring_used() == 0 && resets == resets0 && card_off == CARD_OFF);
        holes_ok = false;
        // a READ of a closed file turns the card on for it; idle 10 s, off again
        data_next = 0;
        send_file(&the_conn, 2 << 16 | (FILES - 1), 0);  // not on the card
        assert(resets == resets0 + CARD_OFF && !card_off && data_next == 0);
        seconds(10, true);
        assert(phone_mode && card_off == CARD_OFF && power_offs == 2 * CARD_OFF);

        // the phone stops acking for 8 s: back to the card, in a new file that starts with what it had not acked
        uint32_t acked = phone_len[s0];
        ack(cur_file(), acked);
        n_logged = 0, holes_ok = true;  // LIVE goes on in the new file, past what the card has of it
        seconds(8, false);
        int e = last_code(LR_PHONE);
        assert(!phone_mode && e >= 0 && logged[e].x == 2 && (logged[e].b & 0xFFFF) == 8000 && seg == s0 + 1);
        assert(logged[e - 1].code == LR_SEGMENT && logged[e - 1].a == cur_file());
        assert(logged[e].a < 10 * 3900 && logged[e].a == card_len[seg]);  // under 10 s of audio not acked
        assert(resets == resets0 + 2 * CARD_OFF && card_len[s0] == on_card);  // the card on again
        flush();
        memset(seen, 0, sizeof(seen));
        mark(phone[s0], acked, f0), mark(card[s0], card_len[s0], f0), mark(card[seg], card_len[seg], f0);
        for (uint32_t f = f0; f < rec_next; f++) {
            assert(seen[f - f0]);  // no frame lost
        }

        // back in phone mode once the phone has READ the new file and acks; stuck at one place, its ACKs on time:
        // 3/4 of the ring not acked, back to the card
        catch_up(0);
        uint32_t stuck = phone_len[seg], f = cur_file();
        n_logged = 0;
        int i = 0;
        for (; i < 15 && phone_mode; i++) {
            frames(rec_next, 100);
            now_ms += 1000;
            ack(f, stuck);  // its own file: the new one it has not seen yet
        }
        e = last_code(LR_PHONE);
        assert(!phone_mode && e >= 0 && logged[e].x == 3 && i >= 8 && logged[e].a < 10 * 3900);
        assert(logged[e].b >> 16 >= RING / 4 * 3 / 1024);

        // the link goes, or the phone unsubscribes from LIVE: back to the card at once
        for (int k = 0; k < 2; k++) {
            catch_up(0);
            seconds(3, false);
            n_logged = 0;
            if (k == 0) {
                linked = false;
            } else {
                subscribed = false;
            }
            tick();
            e = last_code(LR_PHONE);
            uint16_t s = seg;
            assert(!phone_mode && e >= 0 && logged[e].x == 1 && logged[e].a > 2 * 3500 && card_len[s] == logged[e].a);
            linked = subscribed = true;
            next_segment();  // the phone subscribes again: a new file
        }

        // a restart (switch, low battery, update): what the phone has not acked onto the card first
        catch_up(0);
        seconds(3, false);
        n_logged = 0;
        lr_store_shutdown();
        e = last_code(LR_PHONE);
        assert(!phone_mode && e >= 0 && logged[e].x == 4 && logged[e].a > 2 * 3500);
        assert(card_len[seg] > logged[e].a && card_len[seg] == seg_size);  // and the open record after it
        k_mutex_unlock(&write_sdcard_mutex);  // kept for the restart: the next boot's
        next_segment();

        // a crash or a watchdog reset in phone mode: the RAM ring survives it, the next boot writes it to the card as
        // the last boot's next segment
        catch_up(0);
        seconds(3, false);
        uint32_t last = cur_file();
        uint32_t left = ring_used();
        assert(left > 2 * 3500);
        phone_mode = card_off = false, card_file = UINT32_MAX, seg_open = false, n_logged = 0;  // the next boot's RAM
        ring_recover();
        e = last_code(LR_PHONE);
        assert(ring_used() == 0 && e >= 0 && logged[e].x == 5 && logged[e].b == last + 1);
        assert(logged[e].a > 2 * 3500 && logged[e].a < left && card_len[(last + 1) & 0xFFFF] == logged[e].a);
        memset(seen, 0, sizeof(seen));
        uint32_t from = rec_next - 300;
        mark(card[(last + 1) & 0xFFFF], card_len[(last + 1) & 0xFFFF], from);
        uint32_t upto = from;
        while (seen[upto - from]) {
            upto++;
        }
        for (uint32_t f = upto; f < rec_next; f++) {
            assert(!seen[f - from]);
        }
        assert(upto >= rec_next - 110);  // all the ring held; lost, the batch still gathering (~1 s) and the open record
        // a cold start: whatever RAM holds is no ring
        q.magic ^= 1, q.head = 12345;
        ring_recover();
        assert(q.magic == RING_MAGIC && ring_used() == 0 && last_code(LR_PHONE) == e);
    }
    printf("store_check ok: %u + %u + %u B on the card\n", card_len[0], card_len[1], card_len[2]);
    return 0;
}
