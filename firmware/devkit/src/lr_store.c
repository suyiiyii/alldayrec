#include "lr_store.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/bluetooth/uuid.h>
#include <zephyr/fs/fs.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/random/random.h>
#include <zephyr/sys/byteorder.h>
#include <zephyr/sys/ring_buffer.h>

#include "lr_log.h"
#include "lr_power.h"
#include "sdcard.h"
#include "usb.h"
#include "transport.h"

LOG_MODULE_REGISTER(lr_store, CONFIG_LOG_DEFAULT_LEVEL);

extern struct k_mutex write_sdcard_mutex;  // transport.c: one SPI bus, one writer at a time
extern uint32_t transport_frame_no;        // transport.c: frames the codec has made since boot

#define DIR "/SD:/LR"
#define REC_FRAMES 10        // a record per 100 ms: a phone following the stream gets each one as it closes
#define SEG_MAX (16u << 20)  // a long offline stretch still ends in files a phone can take one by one
#define CHUNK 4096           // SD read size while sending a file
#define RESERVE_KB CONFIG_LR_STORE_RESERVE_KB  // recording stops (-ENOSPC) below this much free space, resumes at twice it
#define BATCH CONFIG_LR_STORE_BATCH            // finished records kept in RAM until there are this many bytes

static uint16_t boot;  // 0: no card at boot (nothing to count boots on); the stream still goes to a phone
static int16_t store_err = -ENODEV;  // until lr_store_init succeeds: no card, or it failed
static uint16_t seg;
static uint32_t seg_size;  // the segment's length in the stream: on the card (or lost to it), plus `done` bytes in RAM
uint32_t free_kb;  // the card, as of the last statvfs, less what was written since (lr_log.c heartbeat too)
static uint32_t total_kb;

/* The record stream in RAM: `done` bytes of finished records not yet on their way to the card, then the open 'A'
 * record. Every record the card gets goes to a following phone too (LIVE), with the same file and offset, as it closes.
 * The stream (pend, done, rec_*, seg, seg_size) changes under stream_mtx, which is never held across card I/O: a card
 * that stalls holds up the card writer only. The card itself (seg_file, the card queue's reading end) is the SD
 * mutex's. Both taken: the SD mutex first. */
static uint8_t pend[BATCH + 6 + REC_FRAMES * 256];
static uint32_t done, rec_len, rec_next;  // finished bytes; bytes of the open record; the frame that may extend it
static uint8_t rec_count;
static K_MUTEX_DEFINE(stream_mtx);

static const char *file_path(char *buf, uint32_t file)
{
    snprintf(buf, 32, DIR "/%08X.BIN", file);
    return buf;
}

static uint32_t cur_file(void)
{
    return (uint32_t) boot << 16 | seg;
}

static void restart_segment(void);

/* The card's free space; a full card stops recording until a phone deletes what it took. Store thread or SD mutex. */
static void count_free(void)
{
    struct fs_statvfs v;
    if (fs_statvfs("/SD:", &v) == 0) {
        free_kb = (uint64_t) v.f_bfree * v.f_frsize / 1024;
        total_kb = (uint64_t) v.f_blocks * v.f_frsize / 1024;
    }
    if (store_err == -ENOSPC && free_kb >= 2 * RESERVE_KB) {
        k_mutex_lock(&stream_mtx, K_FOREVER);
        store_err = 0;
        restart_segment();  // the stream went on while the card was full: the card starts a file of its own
        k_mutex_unlock(&stream_mtx);
        LOG_INF("card has room again: %u KB", free_kb);
        lr_log(LR_CARD_FULL, 0, free_kb, 0);
    }
}

uint32_t dropped;            // frames lost on the way to the store (gaps in the frame numbers it got)
static uint16_t append_max;  // the slowest append so far, ms

static struct fs_file_t seg_file;  // the open segment, kept open: reopening walked its FAT chain on every append, slower
static bool seg_open;              // as the file grew (4 KB clusters): ~0.5 s per append at 6 MB, boot 6 of 10-03
static uint32_t card_file = UINT32_MAX, card_at;  // the file seg_file is (or was last), and its bytes on the card
static char card_path[32];

/* Finished batches on their way to the card: the stream puts each one here, its file and offset first, and
 * never waits for the card; the card writer (card_thread) appends them, SD mutex held. A card that stalls fills it
 * (~4 s of audio; ~11 s with a phone that acks) and the batches after go to LIVE only: the stream goes on in a new
 * segment once the writer has caught up, so a file on the card is still the start of the stream's file of that name.
 * While a phone takes the stream (phone mode, below) the batches stay here until it acks them, and the card is left
 * alone. __noinit: a crash or a watchdog reset leaves them for the next boot to write (ring_recover). One writer at a
 * time (stream mutex), one reader at a time (SD mutex): the head moves with the first, the tail with the second. */
#define RING (IS_ENABLED(CONFIG_LR_STORE_PHONE) ? 44 * 1024 : 16 * 1024)
#define RING_MAGIC 0x41C3B10C
static __noinit struct {
    uint32_t magic;
    uint32_t head, tail;  // batches from tail to head; both count on to 2 x RING and round, so a full ring is not empty
    uint8_t b[RING];
} q;
static K_SEM_DEFINE(card_sem, 0, 1);
struct card_rec {
    uint32_t file, offset;
    uint16_t len;
} __packed;
static bool card_behind;  // a batch of this segment did not fit in the queue: no more of it to the card

static uint32_t ring_used(void)
{
    return (q.head + 2 * RING - q.tail) % (2 * RING);
}

/* n bytes at ring position pos (q.tail + how far in), out of (peek) or into (poke) the ring */
static void ring_copy(uint32_t pos, void *d, uint32_t n, bool poke)
{
    for (uint32_t i = 0; i < n;) {
        uint32_t o = (pos + i) % RING, k = MIN(n - i, RING - o);
        if (poke) {
            memcpy(q.b + o, (uint8_t *) d + i, k);
        } else {
            memcpy((uint8_t *) d + i, q.b + o, k);
        }
        i += k;
    }
}

static void ring_drop(uint32_t n)
{
    q.tail = (q.tail + n) % (2 * RING);
}

/* Phone mode (CONFIG_LR_STORE_PHONE): a phone that follows the stream and acks what it has stored (CTRL ACK, every
 * ~5 s) takes it instead of the card. The card gets no more of the open file (on the card still the start of the
 * stream's file of that name) and, idle CARD_IDLE_MS, its supply goes off (CONFIG_LR_STORE_CARD_OFF); the batches stay
 * in the ring until the phone acks them, and a READ of them is answered from it. Back to the card (to_card) when the
 * link goes or the phone unsubscribes, no ACK came for ACK_TIMEOUT_MS, what is not acked fills 3/4 of the ring, a
 * restart or a lend: the batches not acked start a new file, back to back, the stream goes on in it, the card has it
 * whole. The phone may have some of them already (LIVE): the server keeps one copy of each frame. So what the phone
 * has not acked is on the card but for a power cut, which loses at most the ring's 3/4 (~9 s; a reset keeps it,
 * ring_recover). A phone that never acks (older apps, lr_listen.py) leaves the card as it always was. */
#define ACK_TIMEOUT_MS 8000  // the app acks every 5 s; its session loop may be held up a few seconds (a READ's end)
#define CARD_IDLE_MS 10000   // the card on for a LIST, READ or DELETE in phone mode: off again after this long unused
static bool phone_mode;
static bool card_off;          // phone mode turned the card's supply off: card_up() before it is read or written
static int64_t card_used;      // the card last read, written or turned on (k_uptime_get)
static uint32_t ack_file, ack_off;  // the last ACK: the phone has the file up to there (BT RX thread; irq_lock)
static int64_t ack_at = INT64_MIN / 2;
static int64_t followed_at;  // LIVE last subscribed (a new link; BT RX thread, irq_lock): an ACK before is the last link's
static uint32_t unacked_max;   // the most bytes not acked in the ring this time in phone mode
static int card_up(void);
static void to_card(uint8_t why);

/* The open file closed (SD mutex held). A close that fails, as a sync that does (append), may leave the file's length
 * on the card short of what went into it, and a READ ends there: taken as a failed write while the card was working. */
static void close_segment(void)
{
    if (seg_open) {
        int err = fs_close(&seg_file);
        seg_open = false;
        if (err && !store_err) {
            lr_log(LR_APPEND_ERR, 3, err, card_at);
            store_err = err;
        }
    }
}

static bool append_failed;  // only the first failure of a run goes to the event log
static int retries;  // tries of the card (retry_card) since it last took a write (append): a mount alone is not a card that works

/* Appends summed up for 10 minutes into one SD_WRITES event: how long the card keeps the chip busy (power) */
static struct {
    int64_t start;
    uint32_t ms, max, n[4];  // under 20 ms, 20-49, 50-200, over 200
} wr;

/* The SD driver's failed tries since the last time (SD mutex held), if any: with SD_WRITES, and at an append that
 * failed, after which no SD_WRITES may come */
static void log_sd_errors(void)
{
    uint32_t *e = lr_sd_err;
    if (e[LR_SD_CRC] | e[LR_SD_TIMEOUT] | e[LR_SD_OTHER] | e[LR_SD_RETRIED]) {
        lr_log(LR_SD_ERRORS, MIN(e[LR_SD_RETRIED], 0xFF), MIN(e[LR_SD_CRC], 0xFFFF) | MIN(e[LR_SD_TIMEOUT], 0xFFFF) << 16,
               MIN(e[LR_SD_BUSY], 0xFFFF) | MIN(e[LR_SD_OTHER], 0xFFFF) << 16);
        memset(lr_sd_err, 0, sizeof(lr_sd_err));
    }
}

static void count_write(uint32_t ms)
{
    wr.ms += ms;
    wr.max = MAX(wr.max, ms);
    wr.n[ms < 20 ? 0 : ms < 50 ? 1 : ms <= 200 ? 2 : 3]++;
    int64_t now = k_uptime_get();
    if (now - wr.start >= 10 * 60 * 1000) {
        lr_log(LR_SD_WRITES, MIN(wr.n[3], 0xFF), MIN(wr.ms, 0xFFFFF) | MIN(wr.max, 0xFFF) << 20,
               MIN(wr.n[0], 0x3FF) | MIN(wr.n[1], 0x3FF) << 10 | MIN(wr.n[2], 0x3FF) << 20);
        log_sd_errors();
        memset(&wr, 0, sizeof(wr));
        wr.start = now;
    }
}

/* The next segment, if this one has anything (stream mutex held). A file on the card is always the start of the
 * stream's file of that name: after bytes the card did not take (no card, full, lent, a failed write, a card writer
 * behind) it goes on in a new one; the card writer closes the old file when the new one's first batch comes. */
static void restart_segment(void)
{
    card_behind = false;
    if (seg_size) {
        // finished records not on the card are the old file's (LIVE sent them so): not the new one's start
        memmove(pend, pend + done, rec_len);
        done = 0;
        seg++;
        seg_size = 0;
        lr_log(LR_SEGMENT, 0, cur_file(), 0);
    }
}

/* The batch at the ring's tail (len bytes after its header) onto the end of card_path. SD mutex held. */
static int append(uint32_t len)
{
    int64_t t0 = k_uptime_get();
    int err = 0;
    uint32_t taken = 0;
    bool unsynced = false;
    if (free_kb < RESERVE_KB) {
        LOG_ERR("card full (%u KB free): not recording", free_kb);
        lr_log(LR_CARD_FULL, 1, free_kb, 0);
        err = -ENOSPC;
    } else if (!seg_open) {
        fs_file_t_init(&seg_file);
        err = fs_open(&seg_file, card_path, FS_O_CREATE | FS_O_WRITE | FS_O_APPEND);
        seg_open = !err;
    }
    if (!err) {
        int n = 0;
        while (!err && taken < len) {  // the batch may wrap around the ring's end: two writes
            uint32_t o = (q.tail + sizeof(struct card_rec) + taken) % RING, k = MIN(len - taken, RING - o);
            int w = fs_write(&seg_file, q.b + o, k);
            taken += k;
            n += MAX(w, 0);
            err = w == k ? 0 : w < 0 ? w : -EIO;
        }
        int synced = fs_sync(&seg_file);  // on the card now, the file's length too: power can go any time
        if (!err && synced) {
            // the bytes went, the file's length on the card may not have: a READ would end short of them (BDAE 10-13:
            // a card dying, READs ended 11 s to 2 min short of what had gone in, the frames there past the file's end)
            err = synced;
            unsynced = true;
        }
        uint32_t ms = k_uptime_get() - t0;
        append_max = MAX(append_max, (uint16_t) MIN(ms, UINT16_MAX));
        if (ms > 1000) {  // SD_WRITES counts those over 200 ms: one event each filled LR-P1's log in 80 min
            lr_log(LR_SLOW, 0, ms, len);
        }
        count_write(ms);
        free_kb -= MIN(free_kb, DIV_ROUND_UP(n, 1024));
    }
    if (err && !append_failed) {
        lr_log(LR_APPEND_ERR, unsynced ? 3 : 0, err, len);
        log_sd_errors();
    }
    append_failed = err;
    if (!err) {
        retries = 0;
    }
    return err;
}

/* The queued batches onto the card (SD mutex held): the card writer's work, and done first by whatever reads the card
 * (READ of the open segment, LIST, a lend, a reset). A card that fails does not stop the stream: the segment ends there
 * on the card, what is queued after it leaves the queue unwritten once the card is back, and TIME's store reads the
 * error. Meanwhile the queue waits: phone mode's batches not acked, put in a file of their own (to_card) by a card that
 * did not power up, are written once it does (retry_card). */
static void card_drain(void)
{
    struct card_rec h;
    while (!phone_mode && !store_err && ring_used() >= sizeof(h)) {
        ring_copy(q.tail, &h, sizeof(h), false);
        if (h.file != card_file) {  // a new segment: a new file on the card
            close_segment();
            card_file = h.file;
            card_at = 0;
            file_path(card_path, h.file);
        }
        bool skip = store_err || h.offset != card_at;  // the old file's close failed, or a hole in its file
        int err = skip ? 0 : append(h.len);
        ring_drop(sizeof(h) + h.len);  // taken whether or not it made it
        if (skip) {
            continue;
        }
        card_at += h.len;
        if (err) {
            LOG_ERR("SD write failed: %d", err);
            store_err = err;
            close_segment();  // the stream goes on in this segment; the card gets a new one once it works again
        }
    }
}

static void phone_step(void);

static void card_thread(void)
{
    for (;;) {
        // phone mode: once a second too, for the link, the ACKs and the card's idle time
        k_sem_take(&card_sem, IS_ENABLED(CONFIG_LR_STORE_PHONE) ? K_SECONDS(1) : K_FOREVER);
        k_mutex_lock(&write_sdcard_mutex, K_FOREVER);
        card_drain();
        phone_step();
        k_mutex_unlock(&write_sdcard_mutex);
    }
}
// started by lr_store_init, once the ring is the last boot's no more (ring_recover). Phone mode: it mounts the card too
// (to_card, card_up), 1.88 KB at the most (-fcallgraph-info, a blank card's f_mkfs included) plus an FPU frame
K_THREAD_STACK_DEFINE(card_stack, IS_ENABLED(CONFIG_LR_STORE_PHONE) ? 2560 : 2048);
struct k_thread card_thread_data;  // lr_log.c watches its stack

/* The finished records, one batch, to the card writer (stream mutex held). The card's trouble never holds the stream:
 * a batch the queue cannot take goes to LIVE only (APPEND_ERR x 1, -EAGAIN), and so does the rest of the segment; once
 * the writer has caught up, the stream starts a new segment for the card to go on in. */
static void write_out(void)
{
    if (card_behind && !ring_used()) {
        restart_segment();
    }
    if (!done) {
        return;
    }
    if (!store_err && !card_behind) {
        struct card_rec h = {cur_file(), seg_size - done, done};
        if (RING - ring_used() >= sizeof(h) + done) {
            ring_copy(q.head, &h, sizeof(h), true);
            ring_copy(q.head + sizeof(h), pend, done, true);
            __DMB();  // the batch in place before the card writer (or a READ) sees it
            q.head = (q.head + sizeof(h) + done) % (2 * RING);
            k_sem_give(&card_sem);
        } else {
            card_behind = true;
            LOG_ERR("card writer behind: %u B to LIVE only", done);
            lr_log(LR_APPEND_ERR, 1, -EAGAIN, done);
        }
    }
    memmove(pend, pend + done, rec_len);  // the open record moves up
    done = 0;
}

static void live(const uint8_t *b, uint32_t len, uint32_t offset);

/* A finished record into the stream: to a following phone now, to the card with its batch. */
static void put_record(uint32_t len)
{
    live(pend + done, len, seg_size);
    done += len;
    seg_size += len;
}

static void close_record(void)
{
    if (rec_count) {
        pend[done + 1] = rec_count;
        put_record(rec_len);
        rec_len = rec_count = 0;
    }
}

/* The open record closed and everything finished on its way to the card (stream mutex held): before a READ of the
 * open segment, a reset, a lend, a new segment. */
static void flush(void)
{
    close_record();
    write_out();
}

static void next_segment(void)
{
    flush();
    restart_segment();
}

/* A frame from the codec into the stream (the pusher's thread): never waits for the card */
void lr_store_frame(uint32_t frame_no, const uint8_t *data, uint16_t len, bool after_gap)
{
    if (len > 255) {
        return;
    }
    k_mutex_lock(&stream_mtx, K_FOREVER);
    if (rec_count && frame_no > rec_next && !after_gap) {
        dropped += frame_no - rec_next;
        lr_log(LR_DROPPED, 0, frame_no - rec_next, dropped);
    }
    if (rec_count && (frame_no != rec_next || rec_count == REC_FRAMES)) {
        close_record();
        if (done >= BATCH) {
            write_out();
        }
    }
    if (seg_size >= SEG_MAX) {
        next_segment();
    }
    if (!rec_count) {
        pend[done] = 'A';
        sys_put_le32(frame_no, pend + done + 2);
        rec_len = 6;
    }
    pend[done + rec_len++] = len;
    memcpy(pend + done + rec_len, data, len);
    rec_len += len;
    rec_count++;
    rec_next = frame_no + 1;
    k_mutex_unlock(&stream_mtx);
}

/* A 'T' record into the stream (stream mutex held): the phone's clock at the frame the codec is on now. */
static void put_sync(uint32_t frame, uint64_t epoch)
{
    close_record();
    if (done >= BATCH) {
        write_out();
    }
    pend[done] = 'T';
    sys_put_le32(frame, pend + done + 1);
    sys_put_le64(epoch, pend + done + 5);
    put_record(13);
}

//
// Commands, handled on the store's own thread: SD access and notifications may block, the BT RX thread must not
//
enum { LIST = 1, READ = 2, STOP = 3, DELETE = 4, FORGET = 5, USB_DISK = 6, LOG = 7, ACK = 8, SYNC = 0x10, UNLEND = 0x12, FOLLOW = 0x13, PAUSE = 0x20 };
struct cmd {
    uint8_t op;
    uint32_t file, offset;
    uint64_t epoch;
    uint32_t frame;
};
K_MSGQ_DEFINE(cmds, sizeof(struct cmd), 8, 4);

/* A phone starts following the stream: it goes on in a new file, so the phone has that one from offset 0 by LIVE and
 * all before it in closed files it READs in the background; a backlog of days does not hold back what is said now.
 * While the card does not take the stream, nothing before this could be READ anyway. */
static void live_ccc(const struct bt_gatt_attr *attr, uint16_t value)
{
    if (value == BT_GATT_CCC_NOTIFY) {
        unsigned int key = irq_lock();
        followed_at = k_uptime_get();
        irq_unlock(key);
        struct cmd c = {.op = FOLLOW};
        k_msgq_put(&cmds, &c, K_NO_WAIT);
    }
}

static ssize_t time_write(struct bt_conn *conn, const struct bt_gatt_attr *attr, const void *buf, uint16_t len,
                          uint16_t offset, uint8_t flags)
{
    if (len != 8) {
        return BT_GATT_ERR(BT_ATT_ERR_INVALID_ATTRIBUTE_LEN);
    }
    struct cmd c = {.op = SYNC, .epoch = sys_get_le64(buf), .frame = transport_frame_no};
    lr_log(LR_TIME, 0, c.epoch, c.epoch >> 32);
    k_msgq_put(&cmds, &c, K_NO_WAIT);
    return len;
}

/* Why the last boot ended, for a phone to see: RESETREAS, and the fatal error a crash left in RAM that survives a
 * reset (reason, PC; zero when none) */
static uint32_t resetreas, last_fault[2];
static __noinit uint32_t fault[3];  // magic, reason, pc

void k_sys_fatal_error_handler(unsigned int reason, const z_arch_esf_t *esf)
{
    fault[0] = 0xFA017;
    fault[1] = reason;
    fault[2] = esf ? esf->basic.pc : 0;
    NVIC_SystemReset();
}

static ssize_t time_read(struct bt_conn *conn, const struct bt_gatt_attr *attr, void *buf, uint16_t len,
                         uint16_t offset)
{
    extern int16_t usb_diag[4];
    uint8_t v[49];
    sys_put_le16(boot, v);
    sys_put_le32(transport_frame_no, v + 2);
    sys_put_le16(store_err, v + 6);
    sys_put_le32(free_kb, v + 8);
    sys_put_le32(total_kb, v + 12);
    sys_put_le32(resetreas, v + 16);
    sys_put_le32(last_fault[0], v + 20);
    sys_put_le32(last_fault[1], v + 24);
    sys_put_le32(dropped, v + 28);
    sys_put_le16(append_max, v + 32);
    sys_put_le16(usb_diag[0], v + 34);
    sys_put_le16(usb_diag[1], v + 36);
    sys_put_le16(usb_diag[2], v + 38);
    sys_put_le16(usb_diag[3], v + 40);
    v[42] = MIN(lr_bond_count(), UINT8_MAX);
    sys_put_le32(lr_pause_left(), v + 43);
    v[47] = lr_charge();  // a phone reads this byte every few seconds while it reads no backlog
    v[48] = IS_ENABLED(CONFIG_LR_STORE_PHONE);  // bit 0: ACK taken (phone mode); an app acks only then
    return bt_gatt_attr_read(conn, attr, buf, len, offset, v, sizeof(v));
}

static ssize_t ctrl_write(struct bt_conn *conn, const struct bt_gatt_attr *attr, const void *buf, uint16_t len,
                          uint16_t offset, uint8_t flags)
{
    const uint8_t *b = buf;
    struct cmd c = {.op = len ? b[0] : 0};
    if (len >= 5) {
        c.file = sys_get_le32(b + 1);
    }
    if (len >= 9) {
        c.offset = sys_get_le32(b + 5);
    }
    if ((c.op == USB_DISK || c.op == FORGET) && len >= 2) {
        c.file = b[1];  // USB disk on (1) / off (0); forget every phone, this one too (1)
    }
    if (c.op == PAUSE && len >= 5) {  // nothing to do with the card: at once, and on a recorder without one too
        lr_pause(sys_get_le32(b + 1));
        return len;
    }
    if (c.op == ACK && len >= 9) {  // at once too: a command would end a READ going on
        unsigned int key = irq_lock();
        ack_file = c.file;
        ack_off = c.offset;
        ack_at = k_uptime_get();
        irq_unlock(key);
        k_sem_give(&card_sem);
        return len;
    }
    k_msgq_put(&cmds, &c, K_NO_WAIT);
    return len;
}

#define LR_UUID(n) BT_UUID_DECLARE_128(BT_UUID_128_ENCODE(0x7c3e0000 + (n), 0x1a2b, 0x4c5d, 0x8e9f, 0x6c6976657265))

BT_GATT_SERVICE_DEFINE(lr_svc,
                       BT_GATT_PRIMARY_SERVICE(LR_UUID(0)),
                       BT_GATT_CHARACTERISTIC(LR_UUID(1),
                                              BT_GATT_CHRC_READ | BT_GATT_CHRC_WRITE,
                                              BT_GATT_PERM_READ_ENCRYPT | BT_GATT_PERM_WRITE_ENCRYPT,
                                              time_read,
                                              time_write,
                                              NULL),
                       BT_GATT_CHARACTERISTIC(LR_UUID(2),
                                              BT_GATT_CHRC_WRITE | BT_GATT_CHRC_NOTIFY,
                                              BT_GATT_PERM_WRITE_ENCRYPT,
                                              NULL,
                                              ctrl_write,
                                              NULL),
                       BT_GATT_CCC(NULL, BT_GATT_PERM_READ | BT_GATT_PERM_WRITE_ENCRYPT),
                       BT_GATT_CHARACTERISTIC(LR_UUID(3), BT_GATT_CHRC_NOTIFY, BT_GATT_PERM_NONE, NULL, NULL, NULL),
                       BT_GATT_CCC(NULL, BT_GATT_PERM_READ | BT_GATT_PERM_WRITE_ENCRYPT),
                       BT_GATT_CHARACTERISTIC(LR_UUID(4), BT_GATT_CHRC_NOTIFY, BT_GATT_PERM_NONE, NULL, NULL, NULL),
                       BT_GATT_CCC(live_ccc, BT_GATT_PERM_READ | BT_GATT_PERM_WRITE_ENCRYPT), );
#define CTRL_ATTR (&lr_svc.attrs[4])
#define DATA_ATTR (&lr_svc.attrs[7])
#define LIVE_ATTR (&lr_svc.attrs[10])

/* LIVE goes out on a thread of its own (live_thread). The pusher only queues a finished record here, never waiting for
 * Bluetooth with the SD mutex held: a notify waits for a TX buffer, and a link that stalls (going down: up to the
 * supervision timeout; coming up: MTU, security, PHY) held the pusher until the codec's queue overflowed and frames
 * were lost from the card too (2.5-4.5 s at each drop of the X1's link). Now a stalled link fills this queue
 * (~2 s) and the records after are left out of LIVE only: the phone READs the gap from the card. One producer (the
 * pusher, SD mutex held), one consumer: no lock (ring_buf_put publishes the whole record at once). */
#define LIVE_QUEUE (8 * 1024)
RING_BUF_DECLARE(live_rb, LIVE_QUEUE);
static K_SEM_DEFINE(live_sem, 0, 1);
struct live_rec {
    uint32_t file, offset;
    uint16_t len;
} __packed;
static uint32_t live_lost, live_lost_bytes;  // records left out of LIVE in a row: logged once one goes again
static uint32_t live_cut, live_cut_bytes;    // queued records the link lost: logged once the queue is empty

/* n bytes into LIVE's queue, claimed only: the record goes out whole at ring_buf_put_finish */
static void rb_claim(struct ring_buf *rb, const void *d, uint32_t n)
{
    while (n) {
        uint8_t *dst;
        uint32_t k = ring_buf_put_claim(rb, &dst, n);
        memcpy(dst, d, k);
        d = (const uint8_t *) d + k;
        n -= k;
    }
}

static void live(const uint8_t *b, uint32_t len, uint32_t offset)
{
    struct bt_conn *conn = get_current_connection();
    if (!conn || !bt_gatt_is_subscribed(conn, LIVE_ATTR, BT_GATT_CCC_NOTIFY)) {
        return;
    }
    if (ring_buf_space_get(&live_rb) < sizeof(struct live_rec) + len) {
        live_lost++;
        live_lost_bytes += len;
        return;
    }
    struct live_rec h = {cur_file(), offset, len};
    rb_claim(&live_rb, &h, sizeof(h));
    rb_claim(&live_rb, b, len);
    ring_buf_put_finish(&live_rb, sizeof(h) + len);
    k_sem_give(&live_sem);
    if (live_lost) {
        lr_log(LR_LIVE_LOST, 0, live_lost, live_lost_bytes);
        live_lost = live_lost_bytes = 0;
    }
}

/* Notify until it fits in the TX buffers; false once the link is gone. */
static bool notify(struct bt_conn *conn, const struct bt_gatt_attr *attr, const void *data, uint16_t len)
{
    for (;;) {
        int err = bt_gatt_notify(conn, attr, data, len);
        if (err != -ENOMEM) {
            return !err;
        }
        k_sleep(K_MSEC(1));
    }
}

/* The queued records to the phone that follows, split to the MTU, each notify waiting for a TX buffer. Only the
 * link loses a record: one for a link that went (or a phone that unsubscribed) is dropped, and a notify that fails
 * drops the rest of its record; LIVE_LOST x 1 counts them once the queue is empty. A gap the phone fills with READ. */
static void live_send(void)
{
    static uint8_t p[8 + 512];
    struct live_rec h;
    while (ring_buf_get(&live_rb, (uint8_t *) &h, sizeof(h)) == sizeof(h)) {
        struct bt_conn *conn = get_current_connection();
        bool ok = conn && bt_gatt_is_subscribed(conn, LIVE_ATTR, BT_GATT_CCC_NOTIFY);
        if (ok) {
            conn = bt_conn_ref(conn);
            uint16_t room = MIN(bt_gatt_get_mtu(conn) - 3 - 8, sizeof(p) - 8);
            for (uint32_t i = 0; i < h.len; i += room) {
                uint16_t m = MIN(room, h.len - i);
                ring_buf_get(&live_rb, p + 8, m);  // taken either way: the queue goes on with the next record
                sys_put_le32(h.file, p);
                sys_put_le32(h.offset + i, p + 4);
                ok = ok && notify(conn, LIVE_ATTR, p, 8 + m);
            }
            bt_conn_unref(conn);
        } else {
            ring_buf_get(&live_rb, NULL, h.len);
        }
        if (!ok) {
            live_cut++;
            live_cut_bytes += h.len;
        }
    }
    if (live_cut) {
        lr_log(LR_LIVE_LOST, 1, live_cut, live_cut_bytes);
        live_cut = live_cut_bytes = 0;
    }
}

static void live_thread(void)
{
    for (;;) {
        k_sem_take(&live_sem, K_FOREVER);
        live_send();
    }
}
/* The pusher's priority (7), above everything that touches the card (its writer 8, the store 9). Threads of one
 * priority take turns only when one blocks (no time slices: CONFIG_TIMESLICE_SIZE 0), and the SD stack can go on for
 * long without blocking: sdhc_spi.c's sdhc_skip polled 1e6 times for a read's data that never came, NCS's
 * sdmmc_wait_ready busy-waits for a busy card. At 8 with the card writer, LIVE stopped for as long (BDAE 10-09 10:53:
 * 82 s, LIVE_LOST 826 records, the phone connected all along). */
K_THREAD_DEFINE(live_tid, 2048, live_thread, NULL, NULL, NULL, K_PRIO_PREEMPT(7), 0, 0);

static void reply(struct bt_conn *conn, uint8_t op, uint32_t a, uint32_t b, uint8_t blen)
{
    uint8_t v[9] = {op};
    sys_put_le32(a, v + 1);
    sys_put_le32(b, v + 5);
    notify(conn, CTRL_ATTR, v, 5 + blen);
}

static void list(struct bt_conn *conn)
{
    // ponytail: the first 64 files per LIST; a phone deletes what it took and lists again
    static uint32_t files[64][2];
    int n = 0;
    struct fs_dir_t d;
    struct fs_dirent e;
    fs_dir_t_init(&d);
    k_mutex_lock(&write_sdcard_mutex, K_FOREVER);
    int err = fs_opendir(&d, DIR), opened = !err;
    while (!err && n < ARRAY_SIZE(files) && !(err = fs_readdir(&d, &e)) && e.name[0]) {
        uint32_t file = strtoul(e.name, NULL, 16);
        if (e.type == FS_DIR_ENTRY_FILE && strlen(e.name) == 12) {
            files[n][0] = file;
            files[n++][1] = e.size;
        }
    }
    if (opened) {
        fs_closedir(&d);
    }
    k_mutex_unlock(&write_sdcard_mutex);
    for (int i = 0; i < n; i++) {
        reply(conn, 0x81, files[i][0], files[i][1], 4);
    }
    uint8_t end[6] = {0x80, (uint8_t) err};
    sys_put_le32(cur_file(), end + 2);  // the open segment, listed too: the one the stream goes on in
    notify(conn, CTRL_ATTR, end, sizeof(end));
}

static uint8_t chunk[CHUNK];
static uint8_t pkt[4 + 512];

/* Bytes of file at off still in the ring (SD mutex held: the tail stays), up to n; 0: none there */
static uint32_t ring_read(uint32_t file, uint32_t off, uint8_t *dst, uint32_t n)
{
    struct card_rec h;
    uint32_t used = ring_used();
    for (uint32_t at = 0; at + sizeof(h) <= used; at += sizeof(h) + h.len) {
        ring_copy(q.tail + at, &h, sizeof(h), false);
        if (h.file == file && off >= h.offset && off < h.offset + h.len) {
            n = MIN(n, h.offset + h.len - off);
            ring_copy(q.tail + at + sizeof(h) + off - h.offset, dst, n, false);
            return n;
        }
    }
    return 0;
}

/* LOG: records seq.. are at pkt + 4 already (lr_log_read put them there) */
static bool send_log(uint32_t seq, uint16_t n, void *conn)
{
    sys_put_le32(seq, pkt);
    return notify(conn, DATA_ATTR, pkt, 4 + n * 16);
}

/* Files go at a 15 ms interval, the shortest iOS grants a peripheral; a shorter one the phone already has (an Android
 * app asks for its own) is kept. The slow one (30-45 ms), also the GAP preferred parameters Android applies a second
 * into a link, again once no READ came for 5 s: a catch-up is READ after READ. Both within Apple's rules for a
 * peripheral's request (min a multiple of 15 ms, timeout 2-6 s), which the old 7.5-15 ms and its 0.42 s timeout broke. */
#define LINK_FAST BT_LE_CONN_PARAM(12, 12, 0, 400)
#define LINK_SLOW                                                                                                      \
    BT_LE_CONN_PARAM(CONFIG_BT_PERIPHERAL_PREF_MIN_INT, CONFIG_BT_PERIPHERAL_PREF_MAX_INT,                             \
                     CONFIG_BT_PERIPHERAL_PREF_LATENCY, CONFIG_BT_PERIPHERAL_PREF_TIMEOUT)

/* The fast interval, if the link is slower: at a READ's start and on while it goes (the phone may change it, as
 * Android does with the preferred parameters); at most every 5 s, a request takes a while to land */
static void link_fast(struct bt_conn *conn)
{
    static int64_t asked;
    struct bt_conn_info info;
    if (k_uptime_get() - asked > 5000 && !bt_conn_get_info(conn, &info) && info.le.interval > 12) {
        asked = k_uptime_get();
        bt_conn_le_param_update(conn, LINK_FAST);
    }
}

static void link_slow(struct k_work *work)
{
    struct bt_conn *conn = get_current_connection();
    if (conn) {
        bt_conn_le_param_update(conn, LINK_SLOW);
    }
}
static K_WORK_DELAYABLE_DEFINE(slow_work, link_slow);

/* Send one file from offset; a newer command (STOP, another READ) ends it early. Where the time went: READ and
 * READ_TIME events (lr_log.h). */
static void send_file(struct bt_conn *conn, uint32_t file, uint32_t off)
{
    char p[32];
    struct fs_file_t f;
    fs_file_t_init(&f);
    int64_t t0 = k_uptime_get(), t;
    uint32_t from = off, card_ms = 0, send_ms = 0;
    k_work_cancel_delayable(&slow_work);
    link_fast(conn);
    uint16_t payload = MIN(bt_gatt_get_mtu(conn) - 3 - 4, sizeof(pkt) - 4);
    bool ok = true;
    uint32_t end = UINT32_MAX;
    k_mutex_lock(&stream_mtx, K_FOREVER);
    if (file == cur_file()) {  // the open segment: its finished records onto the card, and no further (LIVE has the rest)
        write_out();
        end = seg_size;
    }
    k_mutex_unlock(&stream_mtx);
    k_mutex_lock(&write_sdcard_mutex, K_FOREVER);
    card_drain();  // the queue onto the card before it is read (a card that is behind ends the READ early)
    k_mutex_unlock(&write_sdcard_mutex);
    // still in the ring (phone mode: the open file's last seconds, a LIVE gap) from RAM, the rest from the card. The
    // card file opened once and read on: an open per chunk looked the name up again and its seek walked the cluster
    // chain from the file's start, slower the further in
    int n = 0;
    bool opened = false;
    uint32_t at = UINT32_MAX;  // where the card file is read next
    while (!n && ok && off < end && !k_msgq_num_used_get(&cmds)) {
        k_mutex_lock(&write_sdcard_mutex, K_FOREVER);
        t = k_uptime_get();
        int got = ring_read(file, off, chunk, MIN(CHUNK, end - off));
        if (!got) {
            if (!opened) {
                n = card_up() ?: fs_open(&f, file_path(p, file), FS_O_READ);
                opened = !n;
            }
            if (!n && at != off) {
                n = fs_seek(&f, off, FS_SEEK_SET);
                at = off;
            }
            got = n ?: fs_read(&f, chunk, MIN(CHUNK, end - off));
            at += MAX(got, 0);
            card_used = k_uptime_get();  // a READ of the card longer than CARD_IDLE_MS: on until it ends
        }
        card_ms += k_uptime_get() - t;
        k_mutex_unlock(&write_sdcard_mutex);
        if (got <= 0) {
            break;  // the end (or a read error: the phone sees where it stopped)
        }
        link_fast(conn);
        t = k_uptime_get();
        for (int i = 0; ok && i < got; i += payload) {
            uint16_t m = MIN(payload, got - i);
            sys_put_le32(off + i, pkt);
            memcpy(pkt + 4, chunk + i, m);
            ok = notify(conn, DATA_ATTR, pkt, 4 + m);
        }
        send_ms += k_uptime_get() - t;
        off += got;
    }
    if (opened) {
        k_mutex_lock(&write_sdcard_mutex, K_FOREVER);
        fs_close(&f);
        k_mutex_unlock(&write_sdcard_mutex);
    }
    if (ok) {
        reply(conn, 0x82, file, off, 4);
    }
    if (off > from) {
        struct bt_conn_info info = {0};
        bt_conn_get_info(conn, &info);
        lr_log(LR_READ, MIN(info.le.interval, 0xFF), off - from,
               MIN(k_uptime_get() - t0, 0x3FFFFF) | (uint32_t) payload << 22);
        lr_log(LR_READ_TIME, 0, card_ms, send_ms);
    }
    k_work_reschedule(&slow_work, K_SECONDS(5));
}

struct forget {
    struct bt_conn *keep;
    int *n;
};

/* Every bond but the asking phone's: a lost or replaced phone can no longer connect */
static void forget_bond(const struct bt_bond_info *info, void *arg)
{
    struct forget *f = arg;
    if (!bt_addr_le_eq(&info->addr, bt_conn_get_dst(f->keep))) {
        bt_unpair(BT_ID_DEFAULT, &info->addr);
        (*f->n)++;
    }
}

extern volatile bool usb_disk_lent;  // usb.c: the card reader shows the card
extern volatile uint32_t usb_disk_used;  // usb.c: when the computer last read or wrote it (k_uptime_get_32)
extern bool usb_charge;
static int mount_card(int (*mount)(void));

/* A lent card the computer has not read or written for this long comes back by itself: ejected (macOS's eject
 * never reaches the disk driver), forgotten, or never mounted. Lent, nothing records to the card and the stream goes
 * to LIVE only: a phone gone or a LIVE gap meanwhile was audio kept nowhere (BDAE 10-15: 60 of 107 minutes lent). */
#define LEND_IDLE_MS (3 * 60 * 1000)
enum { BACK_PHONE = 1, BACK_UNPLUGGED, BACK_IDLE };  // why a lent card came back (USB_DISK x 0's b; 0: older firmware)

/* The card to the computer (USB disk) and back (why: BACK_*). Lent, the card is unmounted and nothing records to it
 * (-EBUSY); back, it is mounted again (the ProMicro powers it off and on first) and the card goes on in a new segment.
 * The stream itself never stops. Both ways the board leaves the USB bus and comes back (usb_reattach): back, a
 * computer that has the card mounted lets go of it as of a disk pulled out. A medium that only goes away is not seen:
 * macOS 26 kept the exFAT volume mounted, and whatever read it (cat, diskutil list) hung; unmount force took minutes
 * or failed; the board leaving the bus ended it at once (X1 10-17, back by the phone and back after 3 min alike). */
static int lend(bool on, uint8_t why)
{
    if (on == usb_disk_lent) {
        return 0;
    }
    if (on && (!usb_charge || store_err)) {
        return usb_charge ? store_err : -ENOTCONN;  // not on a computer, or no card to lend
    }
    k_mutex_lock(&write_sdcard_mutex, K_FOREVER);
    if (on) {
        if (phone_mode) {
            to_card(4);
        }
        k_mutex_lock(&stream_mtx, K_FOREVER);
        flush();
        k_mutex_unlock(&stream_mtx);
        card_drain();
        close_segment();
        unmount_sd_card();
        store_err = -EBUSY;
        usb_disk_used = k_uptime_get_32();  // idle from now until the computer reads it
        usb_disk_lent = true;
        LOG_INF("card lent to USB");
        lr_log(LR_USB_DISK, 1, 0, 0);
    } else {
        usb_disk_lent = false;
        int err = mount_card(mount_sd_card);
        k_mutex_lock(&stream_mtx, K_FOREVER);
        store_err = err;
        if (!err) {
            restart_segment();
        }
        k_mutex_unlock(&stream_mtx);
        if (!err) {
            count_free();
        }
        LOG_INF("card back from USB: %d (%d)", err, why);
        lr_log(LR_USB_DISK, 0, err, why);
    }
    k_mutex_unlock(&write_sdcard_mutex);
    if (on || why != BACK_UNPLUGGED) {  // unplugged: off the bus already
        usb_reattach();
    }
    return 0;
}

/* A lent card left alone LEND_IDLE_MS back to the recorder (the store thread looks every RETRY_FAST_MS while lent) */
static void lend_if_idle(void)
{
    if (usb_disk_lent && k_uptime_get_32() - usb_disk_used >= LEND_IDLE_MS) {
        lend(false, BACK_IDLE);
    }
}

static int card_init(int (*mount)(void), uint16_t *counted);

/* A card that failed is tried again RETRY_FAST times RETRY_FAST_MS apart: one that comes back by itself costs seconds
 * of audio, not a minute (BDAE 10-08 14:45: back at the first try, 80 s after its EIO, with no phone to take those
 * 80 s). Then it is reset from scratch (mount_sd_card_reset: a card stuck, BDAE 10-08 11:29, back only by a reboot),
 * once a minute, RETRY_RESETS times; then every 10 minutes, so a dead or missing card is not knocked on for good. A
 * card not there at boot gets the resets alone: one put in later did not mount by plain tries (BDAE 10-14: none at
 * boot 01:50, one put in ~02:2x was not up by 02:31, tried once a minute all the while; the next boot's reset mounted
 * it at once): boot's own reset (sdcard.c, sd_spi_reset) is what wakes a card up properly. The tries count until the
 * card takes a write again, not until it mounts: one that reads but does not write mounted at every quick try, each
 * mount started the count again and the reset never came (BDAE 10-11, writes stuck busy 57 s, then EIO, then
 * mounted again, over and over). A working card is looked at every RETRY_FAST_MS: back by a reset, the next failure
 * is tried again in seconds once it has taken a write, not in 10 minutes. */
#define RETRY_FAST 6
#define RETRY_FAST_MS 5000
#define RETRY_RESETS 3

static int64_t retry_every(void)
{
    int fast = boot ? RETRY_FAST : 0;
    return !store_err || store_err == -EBUSY || retries < fast ? RETRY_FAST_MS : retries < fast + RETRY_RESETS ? 60000 : 600000;
}

/* A card that failed (a write, the mount, none at boot) is tried again (retry_every), one mount try, the SD mutex
 * not held meanwhile (the stream goes on). Back, it goes on in a new segment, as after a lend; a card that was not
 * there at boot counts this boot then and starts at its segment 0. Lent (-EBUSY) and full (-ENOSPC: back when a phone
 * deletes) are not failures. */
static void retry_card(void)
{
    if (!store_err || store_err == -EBUSY || store_err == -ENOSPC) {
        return;
    }
    k_mutex_lock(&write_sdcard_mutex, K_FOREVER);
    close_segment();
    uint16_t was = boot;
    k_mutex_unlock(&write_sdcard_mutex);
    int err;
    unmount_sd_card();  // whatever state the failure left it in
    if (was) {
        err = mount_card(retries < RETRY_FAST ? mount_sd_card_once : mount_sd_card_reset);
    } else {
        err = card_init(mount_sd_card_reset, &was);  // counts the boot
    }
    k_mutex_lock(&write_sdcard_mutex, K_FOREVER);
    retries++;  // a mount too: only a write (append) says the card works
    if (!err) {
        k_mutex_lock(&stream_mtx, K_FOREVER);
        flush();  // the stream's last records go with the file they are in (not on the card)
        if (!boot) {
            boot = was;
            seg = seg_size = 0;
            lr_log(LR_SEGMENT, 0, cur_file(), 0);
        } else {
            restart_segment();
        }
        store_err = 0;
        k_mutex_unlock(&stream_mtx);
        count_free();
        LOG_INF("card back: %08X", cur_file());
    } else if (was) {
        store_err = err;
    }
    k_mutex_unlock(&write_sdcard_mutex);
}

/* The card on and mounted again if phone mode turned it off (SD mutex held): before it is read or written. Not the
 * card's state otherwise (store_err): only this power-up's error */
static int card_up(void)
{
    card_used = k_uptime_get();
    if (!card_off) {
        return 0;
    }
    card_off = false;
    int err = mount_card(mount_sd_card_reset);  // on again (sdcard.c), reset, init, mount; its DIR checked
    if (err) {
        LOG_ERR("card did not come back on: %d", err);
        store_err = err;  // phone mode ends (phone_step), retry_card tries it again
    }
    return err;
}

/* Phone mode over (SD mutex held): the card on, the batches not acked back to back into a new file the card has from
 * its start, the stream on in it, and onto the card now. why: LR_PHONE's x */
static void to_card(uint8_t why)
{
    phone_mode = false;
    k_mutex_lock(&stream_mtx, K_FOREVER);
    write_out();  // the finished records too (LIVE sent them as the old file's), before card_up can fail
    struct card_rec h;
    uint32_t n = 0, used = ring_used();
    for (uint32_t at = 0; at < used; at += sizeof(h) + h.len) {
        ring_copy(q.tail + at, &h, sizeof(h), false);
        h.file = cur_file() + 1;
        h.offset = n;
        ring_copy(q.tail + at, &h, sizeof(h), true);
        n += h.len;
    }
    seg++;
    seg_size = n;
    card_behind = false;
    lr_log(LR_SEGMENT, 0, cur_file(), 0);
    k_mutex_unlock(&stream_mtx);
    card_up();  // a card that does not come on keeps them in the ring (card_drain) until it does (retry_card)
    unsigned int key = irq_lock();
    int64_t since = k_uptime_get() - ack_at;
    irq_unlock(key);
    LOG_INF("phone mode over (%u): %u B not acked to %08X", why, n, cur_file());
    lr_log(LR_PHONE, why, n, MIN(since, 0xFFFF) | MIN(unacked_max / 1024, 0xFFFF) << 16);
    card_drain();
}

/* Phone mode in and out (the card writer, SD mutex held, the card caught up): at each batch and ACK, and once a
 * second. In: the phone follows the stream and has acked the open file, close behind it, and the card works. */
static void phone_step(void)
{
    if (!IS_ENABLED(CONFIG_LR_STORE_PHONE)) {
        return;
    }
    struct bt_conn *conn = get_current_connection();
    bool following = conn && bt_gatt_is_subscribed(conn, LIVE_ATTR, BT_GATT_CCC_NOTIFY);
    unsigned int key = irq_lock();
    uint32_t af = ack_file, ao = ack_off;
    int64_t since = k_uptime_get() - ack_at;
    following = following && ack_at > followed_at;  // a quick reconnect: the link went (and its ACKs with it)
    irq_unlock(key);
    if (!phone_mode) {
        if (following && since < ACK_TIMEOUT_MS && !store_err && !ring_used()) {
            k_mutex_lock(&stream_mtx, K_FOREVER);
            uint32_t behind = af == cur_file() && ao <= seg_size ? seg_size - ao : UINT32_MAX;
            k_mutex_unlock(&stream_mtx);
            if (behind < RING / 2) {
                close_segment();  // the card's copy of the open file ends here
                phone_mode = true;
                card_used = k_uptime_get();
                unacked_max = 0;
                LOG_INF("phone mode: %08X, the phone %u B behind", cur_file(), behind);
                lr_log(LR_PHONE, 0, behind, 0);
            }
        }
        return;
    }
    // what the phone has: out of the ring. An ACK says nothing of the files before it: LIVE may have lost a file's
    // last seconds just before a new one (SEG_MAX, a quick reconnect's FOLLOW), and its LIST shows the card's start of
    // it only, so the phone takes it for whole. Those batches stay, and go to the card (3/4 of the ring at the latest)
    struct card_rec h;
    while (ring_used() >= sizeof(h)) {
        ring_copy(q.tail, &h, sizeof(h), false);
        if (h.file != af || h.offset + h.len > ao) {
            break;
        }
        ring_drop(sizeof(h) + h.len);
    }
    unacked_max = MAX(unacked_max, ring_used());
    uint8_t why = !following ? 1 : since >= ACK_TIMEOUT_MS ? 2 : ring_used() > RING / 4 * 3 ? 3 : store_err ? 6 : 0;
    if (why) {
        to_card(why);
    } else if (IS_ENABLED(CONFIG_LR_STORE_CARD_OFF) && !card_off && k_uptime_get() - card_used >= CARD_IDLE_MS) {
        unmount_sd_card();
        sd_power_off();
        card_off = true;
    }
}

static void store_thread(void)
{
    struct cmd c;
    int64_t tried = k_uptime_get();
    for (;;) {
        int got = k_msgq_get(&cmds, &c, K_MSEC(retry_every()));
        if (k_uptime_get() - tried >= retry_every()) {
            tried = k_uptime_get();
            retry_card();
        }
        lend_if_idle();
        if (got) {
            continue;
        }
        if (c.op == UNLEND) {
            lend(false, BACK_UNPLUGGED);
            continue;
        }
        struct bt_conn *conn = get_current_connection();
        if (c.op == FOLLOW) {
            k_mutex_lock(&stream_mtx, K_FOREVER);
            next_segment();  // nothing new if the segment is empty
            k_mutex_unlock(&stream_mtx);
            continue;
        }
        if (c.op == SYNC) {  // into the stream, where it is: no new segment
            k_mutex_lock(&stream_mtx, K_FOREVER);
            put_sync(c.frame, c.epoch);
            k_mutex_unlock(&stream_mtx);
            LOG_INF("sync: frame %u = %llu", c.frame, c.epoch);
            continue;
        }
        if (!conn) {
            continue;
        }
        conn = bt_conn_ref(conn);
        if (c.op == LIST) {
            // the open segment is listed with what the stream has finished: written out first
            k_mutex_lock(&stream_mtx, K_FOREVER);
            write_out();
            k_mutex_unlock(&stream_mtx);
            k_mutex_lock(&write_sdcard_mutex, K_FOREVER);
            card_drain();
            card_up();
            count_free();
            k_mutex_unlock(&write_sdcard_mutex);
            list(conn);
        } else if (c.op == READ) {
            send_file(conn, c.file, c.offset);
        } else if (c.op == USB_DISK) {
            int err = lend(c.file, BACK_PHONE);
            reply(conn, 0x86, c.file, (uint8_t) err, 1);
        } else if (c.op == FORGET && c.file) {  // unbinding: the reply first, the link goes with this phone's bond
            reply(conn, 0x85, lr_bond_count(), 0, 0);
            bt_unpair(BT_ID_DEFAULT, BT_ADDR_LE_ANY);
        } else if (c.op == FORGET) {
            int n = 0;
            bt_foreach_bond(BT_ID_DEFAULT, forget_bond, &(struct forget){conn, &n});
            lr_log(LR_FORGET, 0, n, 0);
            reply(conn, 0x85, n, 0, 0);
        } else if (c.op == LOG) {
            uint16_t max = MIN(bt_gatt_get_mtu(conn) - 3 - 4, sizeof(pkt) - 4) / 16;
            reply(conn, 0x87, lr_log_read(c.file, pkt + 4, max, send_log, conn), 0, 0);
        } else if (c.op == DELETE) {
            char p[32];
            int err = -EBUSY;
            if (c.file != cur_file()) {
                k_mutex_lock(&write_sdcard_mutex, K_FOREVER);
                err = card_up() ?: fs_unlink(file_path(p, c.file));
                count_free();
                k_mutex_unlock(&write_sdcard_mutex);
            }
            reply(conn, 0x84, c.file, (uint8_t) err, 1);
        }
        bt_conn_unref(conn);
    }
}
K_THREAD_STACK_DEFINE(store_stack, 3072);  // 2048 left 752 B unused (event log STACKS, 10-04)
struct k_thread store_thread_data;  // lr_log.c watches its stack

void lr_store_shutdown(void)
{
    // kept: nothing writes the card after this. Not forever: a card stuck in a write holds the mutex for good (BDAE
    // 10-08: no card write for 10+ min, the watchdog fed all the while), and every way to restart comes through here
    // (switch, 1200 bps touch, BLE DFU, low battery); past 5 s the restart goes ahead without the card's last records
    if (k_mutex_lock(&write_sdcard_mutex, K_SECONDS(5))) {
        lr_log(LR_APPEND_ERR, 2, -EDEADLK, 0);
        return;
    }
    if (phone_mode) {
        to_card(4);  // what the phone has not acked: onto the card first
    }
    if (!store_err) {
        k_mutex_lock(&stream_mtx, K_FOREVER);
        flush();
        k_mutex_unlock(&stream_mtx);
        card_drain();
        close_segment();
        unmount_sd_card();
    }
}

int lr_store_err(void)
{
    return store_err;
}

void lr_store_lend(bool on)
{
    if (!on) {
        struct cmd c = {.op = UNLEND};
        k_msgq_put(&cmds, &c, K_NO_WAIT);
    }
}

/* What a crash or a watchdog reset left in the ring (__noinit; a cold start's RAM fails the checks): the batches no
 * phone acked or the card had not taken yet, back to back into a file of their own, the last boot's segment FFFF. Not
 * its next one: the stream may have gone on in that (SEG_MAX, a FOLLOW) with none of it in the ring yet, and LIVE sent
 * its start; the phone would put the two together. A boot gets to FFFF only after 65535 files (one per SEG_MAX,
 * reconnect or card trouble). Without a card they are lost; the ring is this boot's from here.
 * No CRC: the magic, the bounds and the batches' headers are the checks. The ring's place is the linker's (.noinit, by
 * lr_log's): a firmware that puts it elsewhere reads another's bytes there, and those checks turn them down. */
static void ring_recover(void)
{
    struct card_rec h;
    uint32_t end = 0, file = 0, n = 0;
    if (q.magic != RING_MAGIC || q.head >= 2 * RING || q.tail >= 2 * RING || ring_used() > RING) {
        q.magic = RING_MAGIC;
        q.head = q.tail = 0;
        return;
    }
    for (uint32_t used = ring_used(); end + sizeof(h) <= used; end += sizeof(h) + h.len) {
        ring_copy(q.tail + end, &h, sizeof(h), false);
        if (!h.len || h.len > sizeof(pend) || end + sizeof(h) + h.len > used || (end && h.file >> 16 != file >> 16)) {
            break;  // not a batch: what follows is not either
        }
        file = h.file;
    }
    q.head = (q.tail + end) % (2 * RING);
    if (store_err || !end) {
        q.tail = q.head;
        return;
    }
    for (uint32_t at = 0; at < end; at += sizeof(h) + h.len) {
        ring_copy(q.tail + at, &h, sizeof(h), false);
        h.file = file | 0xFFFF;
        h.offset = n;
        ring_copy(q.tail + at, &h, sizeof(h), true);
        n += h.len;
    }
    card_drain();
    close_segment();
    LOG_INF("%u B the last boot left in RAM: %08X", n, file | 0xFFFF);
    lr_log(LR_PHONE, 5, n, file | 0xFFFF);
}

/* Mount the SD card, count this boot, pick its first segment, write what the last boot left in RAM; start the card
 * writer and the command thread, card or not (the stream, its 'T' records and LIVE go on without one). */
int lr_store_init(void)
{
    resetreas = NRF_POWER->RESETREAS;
    NRF_POWER->RESETREAS = resetreas;  // write-1-to-clear: the next boot sees only its own
    if (fault[0] == 0xFA017) {
        last_fault[0] = fault[1];
        last_fault[1] = fault[2];
        fault[0] = 0;
        lr_log(LR_FAULT, 0, last_fault[0], last_fault[1]);
    }
    int err = card_init(mount_sd_card, &boot);
    store_err = err;
    if (err) {
        seg = sys_rand32_get();  // boot 0 has no count: a random first segment keeps this boot's files apart from another's
    }
    ring_recover();
    k_thread_create(&card_thread_data, card_stack, K_THREAD_STACK_SIZEOF(card_stack), (k_thread_entry_t) card_thread,
                    NULL, NULL, NULL, K_PRIO_PREEMPT(8), 0, K_NO_WAIT);  // above READs (the store, 9)
    k_thread_create(&store_thread_data, store_stack, K_THREAD_STACK_SIZEOF(store_stack),
                    (k_thread_entry_t) store_thread, NULL, NULL, NULL,
                    K_PRIO_PREEMPT(9), 0, K_NO_WAIT);  // below the pusher (7): sending a file never starves recording
    return err;
}

/* The audio's boot numbers name its files (boot << 16 | segment) and the app keeps TIME records and files by them, for
 * good: a number must never come back. Counted on the card alone it did: a card that lost its /LR (BDAE 10-13 11:18, a
 * dying card: BOOT read back nothing) started again at 1, and the app timed the new audio by 10-10's records of boot 1.
 * So a boot also counts past the newest file on the card and past the chip's own count (lr_log, in its flash)
 * plus BOOT_FLOOR, above anything a card counted before (815 at most, 10-09). A card that is falling apart says
 * anything (BDAE's /LR was 105 entries of garbage): a count more than BOOT_SLACK past the chip's, or one that would not
 * fit 16 bits, is not taken; nor is a name other than 8 hex digits and .BIN. */
#define BOOT_FLOOR 4096
#define BOOT_SLACK 1000

static bool our_file(const char *name)  /* BBBBSSSS.BIN, as file_path() writes it */
{
    for (int i = 0; i < 8; i++) {
        if (!((name[i] >= '0' && name[i] <= '9') || (name[i] >= 'A' && name[i] <= 'F'))) {
            return false;
        }
    }
    return !strcmp(name + 8, ".BIN");
}

static uint32_t newest_file_boot(void)
{
    struct fs_dir_t d;
    struct fs_dirent e;
    uint32_t newest = 0;
    fs_dir_t_init(&d);
    if (fs_opendir(&d, DIR)) {
        return 0;
    }
    while (!fs_readdir(&d, &e) && e.name[0]) {
        if (e.type == FS_DIR_ENTRY_FILE && our_file(e.name)) {
            newest = MAX(newest, strtoul(e.name, NULL, 16) >> 16);
        }
    }
    fs_closedir(&d);
    return newest;
}

/* The next boot: past the card's count and its newest file, never below the chip's count + BOOT_FLOOR. *wild: a count
 * on the card was too far past the chip's to be real (garbage) and was left out. */
static uint16_t next_boot(uint32_t on_card, uint32_t newest, uint32_t chip, bool *wild)
{
    uint32_t floor = chip + BOOT_FLOOR, next = floor;
    for (int i = 0; i < 2; i++) {
        uint32_t v = i ? newest : on_card;
        if (v > floor + BOOT_SLACK || v + 1 > 0xFFFF) {
            *wild = true;
        } else {
            next = MAX(next, v + 1);
        }
    }
    return MIN(next, 0xFFFF);
}

/* Mount the card (boot's mount, one try, from scratch) and set it up: its DIR, and a computer the card is lent to (USB
 * disk) leaves it alone: no Spotlight index, no fseventsd log. Every mount, not boot's alone: a card wiped on the
 * computer it was lent to (X1 10-17: erased as exFAT) came back without its DIR, mounted at every try, and each
 * write failed (-ENOENT) until a reboot; so would a blank card put in while it runs. */
static int mount_card(int (*mount)(void))
{
    int err = mount();
    if (err) {
        return err;
    }
    struct fs_file_t m;
    fs_file_t_init(&m);
    if (!fs_open(&m, "/SD:/.metadata_never_index", FS_O_CREATE | FS_O_WRITE)) {
        fs_close(&m);
    }
    fs_mkdir("/SD:/.fseventsd");
    fs_file_t_init(&m);
    if (!fs_open(&m, "/SD:/.fseventsd/no_log", FS_O_CREATE | FS_O_WRITE)) {
        fs_close(&m);
    }
    err = fs_mkdir(DIR);
    if (err && err != -EEXIST) {
        LOG_ERR("mkdir " DIR ": %d", err);
        return err;
    }
    return 0;
}

/* Mount, set the card up, count this boot into *counted. The caller sets store_err (and boot, under the SD mutex). */
static int card_init(int (*mount)(void), uint16_t *counted)
{
    struct fs_file_t f;
    fs_file_t_init(&f);
    int err = mount_card(mount);
    if (err) {
        return err;
    }
    err = fs_open(&f, DIR "/BOOT", FS_O_CREATE | FS_O_RDWR);
    if (err) {
        return err;
    }
    uint8_t b[2] = {0};
    bool unread = fs_read(&f, b, 2) != 2, wild = false;
    *counted = next_boot(sys_get_le16(b), newest_file_boot(), lr_log_boot(), &wild);
    sys_put_le16(*counted, b);
    fs_seek(&f, 0, FS_SEEK_SET);
    bool unwritten = fs_write(&f, b, 2) != 2;
    fs_close(&f);
    count_free();
    LOG_INF("boot %u, %u of %u KB free", *counted, free_kb, total_kb);
    lr_log(LR_STORE, unread | unwritten << 1 | wild << 2, *counted, free_kb);
    return 0;
}
