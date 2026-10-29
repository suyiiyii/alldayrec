#ifndef LR_LOG_H
#define LR_LOG_H

#include <stdbool.h>
#include <stdint.h>

/*
 * live-record event log, the recorder's black box: what happened before a fault, kept in the chip's own flash (not
 * on the SD card, whose faults it records) and read over BLE (CTRL 0x07 LOG, lr_store.h).
 *
 * A record, 16 B little endian:  boot:u16 t:u32 code:u8 x:u8 a:u32 b:u32
 *   boot  this log's boot count (each start of the firmware, whatever the card says)
 *   t     the codec frame the event happened at, the audio's clock (10 ms a frame, put on the wall clock by the boot's
 *         TIME events); before the codec's first frame code has bit 7 (LR_UPTIME) set and t is uptime ms
 *   code, x, a, b  the event and its arguments:
 *     1 BOOT        x battery mV / 20, a RESETREAS (why the last boot ended), b firmware hash (lr.sh's rev; bit 28 dirty)
 *     2 FAULT       a reason, b PC of the crash that ended the previous boot
 *     3 MOUNT       x try, a error: one per SD mount try at boot; x 0: a failed card tried again (lr_store.c retry_card),
 *                   when it mounts, and when a try from scratch (after an SD_RESET) fails
 *     4 STORE       a the audio's boot number (file names BBBBSSSS), b free KB: the offline store is up; x bit 0: the
 *                   card's BOOT did not read back (2 bytes), bit 1: the new count did not write to it, bit 2: a count
 *                   on the card (BOOT, a file name) too far past the chip's own to be real was left out
 *     5 SLOW        a ms, b bytes: an SD append slower than 1000 ms
 *     6 APPEND_ERR  a error, b bytes: the first failed SD append of a run; x 0 the write, 1 the card writer behind
 *                   (-EAGAIN, the rest of the segment to LIVE only), 2 a restart with the card stuck in a write
 *                   (-EDEADLK), 3 the file's length on the card (fs_sync, or fs_close with b the file's bytes)
 *     7 CARD_FULL   x 1 recording stopped / 0 resumed, a free KB
 *     8 USB_DISK    x 1 card lent to USB / 0 back, a error, b (back) why: 1 the phone, 2 unplugged, 3 the computer had
 *                   not read or written it for 3 minutes (0: firmware older than this field)
 *     9 CONNECT     a interval (1.25 ms) | latency << 16, b supervision timeout (10 ms)
 *    10 DISCONNECT  x HCI reason
 *    11 PARAM       a interval | latency << 16, b timeout: connection parameters changed
 *    12 MTU         x 0: ATT MTU, 1: link data length; a tx, b rx
 *    13 PHY         a tx PHY, b rx PHY (1: 1M, 2: 2M, 4: coded)
 *    14 TIME        a | b << 32: epoch ms the phone said frame t was at
 *    15 SECURITY    x level, a error
 *    16 PAIRED      x bonded
 *    17 PAIR_FAIL   x reason (enum bt_security_err)
 *    18 FORGET      x 0: a phone asked (FORGET), 1: a stale bond dropped; a bonds removed
 *    19 DROPPED     a frames lost on the way to the card (the mic's 1 s ring full, mic_ring.h; the TX queue full), b this
 *                   boot's total
 *    20 SEGMENT     a the new open file (boot << 16 | segment)
 *    21 DFU         x GPREGRET: 0x57 UF2 bootloader, 0xA8 OTA bootloader
 *    22 HEARTBEAT   x battery mV / 20, a free card KB, b frames dropped this boot (every 10 min)
 *    23 STACKS      a codec << 16 | pusher, b store << 16 | BT RX: bytes of stack never used (0xFFFF: no thread);
 *                   x 1: a the card writer << 16 | the system workqueue (switch, low battery, 1200 bps touch)
 *    24 SD_RESET    a R1 to CMD12, b R1 to CMD0 (0x01: idle) of the SPI reset before the first mount, and before each
 *                   try from scratch of a failed card
 *    25 SD_WRITES   the card appends of the last 10 minutes, by the first append after them: x how many took over
 *                   200 ms (over 1000 ms each a SLOW too), a ms all took (bits 0-19) | the slowest's ms << 20, b how
 *                   many took under 20 ms | 20-49 ms << 10 | 50-200 ms << 20; every field saturates
 *    26 READ        a READ's end (one that sent anything): x connection interval then (1.25 ms, 255: longer),
 *                   a bytes sent, b ms it took (bits 0-21) | bytes a DATA notification carries << 22
 *    27 READ_TIME   that READ's ms on the card (a: open, seek, fs_read) and in notify (b: sending, waiting for a TX
 *                   buffer); the rest of READ's ms is the SD mutex (recording's appends) and the open file's write-out
 *    28 SWITCH      LR-P3's slide switch (lr_switch.c), each position held 0.5 s: x 1 on / 0 off, a what follows:
 *                   0 recording, 1 System OFF (up on USB, the cable gone), 2 up on USB, not recording, 3 a restart
 *                   (switched while running or up on USB). A boot that finds it off, or not on for 0.5 s within 3 s,
 *                   goes to System OFF with no SWITCH: its BOOT is the only record. The wake from System OFF is the
 *                   next BOOT's RESETREAS (OFF: the switch, VBUS: USB)
 *    29 POWER       power (lr_power.c): x battery mV / 20 (LR-P3: VDDH), a 0: once a minute on battery (LR-P3), 1: the
 *                   chip's power-fail warning (LR-P3: VDDH under 3.3 V or VDD under 2.8 V; x 0, not measured in the
 *                   interrupt), 2: low battery, recording ended, System OFF next until USB (or LR-P3's switch) wakes it
 *    30 LIVE_LOST   a records, b bytes left out of LIVE; on the card all the same, the phone READs them. x 0: in a
 *                   row, its queue full (the link stalled), by the next one that goes; x 1: queued, then lost
 *                   to the link (gone, or the phone unsubscribed; a record cut short counts whole), once the queue is
 *                   empty
 *    31 HEALTH      the mic and the chip over the last 10 minutes, with HEARTBEAT: x die temperature (°C, signed),
 *                   a ms of all-zero 10 ms frames from the mic (bits 0-19) | PDM errors << 20, b samples at full scale
 *                   (bits 0-23) | the loudest sample after the high-pass >> 7 << 24 (255: full scale); zeros and full
 *                   scale are counted before the high-pass; every field saturates
 *    32 CARD_ID     the card's CID at each mount (no serial number): x manufacturer ID, a product name characters 1-4
 *                   (the first in bits 0-7), b its 5th | OEM ID << 8 (two characters, the first in bits 8-15) |
 *                   product revision << 24 (BCD n.m)
 *    33 CARD_SIZE   with CARD_ID: x CSD structure (0: SDSC, 1: SDHC/SDXC), a 512 B sectors, b manufacture date from the
 *                   CID (years since 2000 << 4 | month)
 *    34 SD_ERRORS   the SD driver's failed tries in the last 10 minutes, with SD_WRITES, only when there were any: x
 *                   transfers tried again, a CRC errors (bits 0-15) | timeouts << 16 (no answer, or busy past its
 *                   deadline), b of those timeouts the busy ones (bits 0-15) | other errors << 16 (an error token or
 *                   R1); every field saturates
 *    35 PHONE       phone mode (lr_store.c): x 0 a phone that acks took the stream, the card left alone from here, a
 *                   bytes the stream was past its ACK; x 1-4, 6 back to the card, a bytes not acked, written as a new
 *                   file (the SEGMENT before it), b ms since the last ACK (bits 0-15) | the most KB not acked in that
 *                   time << 16; why: 1 the link gone or LIVE unsubscribed, 2 no ACK for 8 s, 3 3/4 of the RAM ring not
 *                   acked, 4 a restart or the card lent to USB, 6 the card did not come back on. x 5: at boot, a bytes
 *                   a crash or watchdog reset left in RAM, written to the card as file b
 *
 * Each record has a sequence number, one more than the last, across boots: a reader asks for those from the last
 * it has. Records gather in RAM (__noinit: a crash or watchdog reset keeps them for the next boot) and go to flash
 * in batches, one FCB entry (seq:u32 of the first, then the records) per batch, in the 16 KB lr_log partition below
 * the bootloader: ~1000 records. Errors, a boot and a reset into the bootloader are written at once, the rest when
 * half the RAM batch is full or every 5 minutes.
 */

enum {
    LR_BOOT = 1, LR_FAULT, LR_MOUNT, LR_STORE, LR_SLOW, LR_APPEND_ERR, LR_CARD_FULL, LR_USB_DISK, LR_CONNECT,
    LR_DISCONNECT, LR_PARAM, LR_MTU, LR_PHY, LR_TIME, LR_SECURITY, LR_PAIRED, LR_PAIR_FAIL, LR_FORGET, LR_DROPPED,
    LR_SEGMENT, LR_DFU, LR_HEARTBEAT, LR_STACKS, LR_SD_RESET, LR_SD_WRITES, LR_READ, LR_READ_TIME, LR_SWITCH,
    LR_POWER, LR_LIVE_LOST, LR_HEALTH, LR_CARD_ID, LR_CARD_SIZE, LR_SD_ERRORS, LR_PHONE,
    LR_UPTIME = 0x80,
};

/* Find where the flash log ends, write what the last boot left in RAM, log this boot. Battery up first. */
int lr_log_init(void);
/* Any thread; never blocks. */
void lr_log(uint8_t code, uint8_t x, uint32_t a, uint32_t b);
/* This boot's count in the log (the chip's flash: a card swapped or lost does not reset it). After lr_log_init. */
uint16_t lr_log_boot(void);
/* Into the bootloader (GPREGRET 0x57 UF2, 0xA8 OTA), the log written first. */
void lr_log_reset(uint8_t gpregret);
/* The RAM batch to flash now, on this thread: before a reset or System OFF. */
void lr_log_sync(void);
/* Everything from seq `from` on, to send() at most max records at a time (consecutive seqs, read into buf); stops
 * when send() returns false. Returns the seq the next record will get. */
uint32_t lr_log_read(uint32_t from, uint8_t *buf, uint16_t max,
                     bool (*send)(uint32_t seq, uint16_t n, void *arg), void *arg);

#endif
