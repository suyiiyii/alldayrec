#ifndef LR_STORE_H
#define LR_STORE_H

#include <stdbool.h>
#include <stdint.h>

/*
 * live-record record stream: everything the mic records, as records, written to the SD card and sent to a phone that
 * follows it, the same bytes at the same file and offset. The device has no clock; the mic does: frame n of a boot was
 * captured 10 ms after frame n-1, so a frame's time is its boot's nearest sync record (frame f was epoch e) +
 * (n - f) * 10 ms. The phone is the clock: its TIME writes become the 'T' records.
 *
 * Files: /SD:/LR/BBBBSSSS.BIN (hex boot number, segment). A segment ends at 16 MB, at a reboot, where the card
 * could not take the stream (no card, full, lent to USB, a failed write; a failed card is tried again every minute),
 * and where phone mode ends (below): a file on the card is always the start of the stream's file of that name. Boot 0:
 * there was no card at boot to count boots on; its first segment number is random, so two such boots do not share file
 * names. Records, little endian:
 *   'A' count:u8 first_frame:u32  then count x (len:u8 opus[len])   consecutive frames first_frame, +1, ... (<= 10)
 *   'T' frame:u32 epoch_ms:u64                                      the phone said frame `frame` was at epoch_ms
 * Finished records gather in RAM (CONFIG_LR_STORE_BATCH, ~1 s) and go to the card in one write; a power cut loses at
 * most those (a crash or watchdog reset keeps the batches queued: the next boot writes them as a file of their own). A
 * file cut short by power loss ends in a partial record: readers stop there.
 *
 * Phone mode (CONFIG_LR_STORE_PHONE, LR-P3): a phone that follows the stream and acks it (ACK) takes it instead of the
 * card. The card's copy of the open file ends there and the card is powered off; the batches stay in RAM until acked,
 * and a READ of them is answered from RAM. When the link goes, LIVE is unsubscribed, no ACK comes for 8 s or RAM is
 * 3/4 full (~9 s not acked), the batches not acked start a new segment, back to back, and the card takes the stream
 * again from there: some of those frames the phone may have had already, under the old file's name. A phone that
 * never acks leaves the card as without it.
 *
 * GATT service 7c3e0000-1a2b-4c5d-8e9f-6c6976657265:
 *   ...01 TIME  write epoch_ms:u64 (phone's clock: on connect, and now and then while connected) -> a 'T' record for the
 *               frame the codec is on, into the stream at once; read -> boot:u16 frame:u32 (the frame now) store:i16
 *               (0: storing; else -errno: -19 no card, -28 card full, -16 lent to USB, -5 a write failed) free_kb:u32
 *               total_kb:u32 resetreas:u32 (the last reset's cause) fault_reason:u32 fault_pc:u32 (a crash before it;
 *               0: none) dropped:u32 (frames that never reached the store this boot) append_max_ms:u16 (slowest write)
 *               usb_diag:4 x i16 bonds:u8 (phones bonded; 0: unbound, not recording) pause_left_s:u32 (0: recording)
 *               charge:u8 (lr_power.h) features:u8 (bit 0: ACK taken, phone mode; older firmware: no such byte)
 *   ...02 CTRL  write commands, notify replies:
 *               0x01 LIST                -> 0x81 file:u32 size:u32 per file (the open one too, with what the stream
 *                                           has finished), then 0x80 err:i8 open:u32 (the file the stream goes on in)
 *               0x02 READ file:u32 offset:u32 -> DATA notifications, then 0x82 file:u32 end:u32; the open file up to
 *                                           what the stream has finished when the READ starts
 *               0x03 STOP
 *               0x04 DELETE file:u32     -> 0x84 file:u32 status:i8 (the open file is refused: -16)
 *               0x05 FORGET              -> 0x85 n:u32: every bonded phone but this one is forgotten
 *               0x05 0x01 FORGET all     -> 0x85 n:u32, then this phone is forgotten too and the link goes (unbinding)
 *               0x06 USB_DISK on:u8      -> 0x86 on:u32 status:i8: on a computer's USB, the card shows as a USB disk
 *                                           (writing to it pauses, TIME's store reads -16; the stream goes on) or comes
 *                                           back (eject it on the computer first); unplugging also brings it back.
 *                                           -107: not on USB
 *               0x07 LOG from:u32        -> DATA notifications seq:u32 + records (seq, seq+1, ...; 16 B each, lr_log.h)
 *                                           for every logged event from seq `from` on, then 0x87 next:u32, the seq
 *                                           the next event will get: ask from there next time
 *               0x08 ACK file:u32 offset:u32  the phone has stored `file` up to `offset`, all of it; of other files
 *                                           it says nothing (every ~5 s while it follows; features bit 0 only). Taken
 *                                           at once, a READ going on goes on. No reply: the write's response
 *               0x20 PAUSE s:u32         nothing kept for s seconds (frames still counted), then recording again by
 *                                           itself; 0 resumes now; a reset resumes too. No reply: the write's response
 *   ...03 DATA  notify offset:u32 bytes...                 (READ)
 *   ...04 LIVE  notify file:u32 offset:u32 bytes...        each record as it enters the stream, while subscribed;
 *               a gap in the offsets (not subscribed, the link busy or gone) is the phone's to READ. Subscribing
 *               starts a new file (when the open one has anything): the phone gets it from offset 0, and all before
 *               it is in closed files to READ alongside
 * All but battery and device info need an encrypted (bonded) link; see pairing in transport.c. Nothing is recorded
 * while no phone is bonded or while paused (transport.c): no records, the frame numbers skip that time. The scan
 * response's manufacturer data (0xFFFF) carries the state byte.
 * file = boot << 16 | segment.
 */

int lr_store_init(void);
/* One encoded frame, numbered by the codec since boot; called with the SD mutex held. after_gap: frames before it were
 * left out on purpose (unbound, paused), not lost. */
void lr_store_frame(uint32_t frame_no, const uint8_t *data, uint16_t len, bool after_gap);
/* USB unplugged: take the card back if it was lent (usb.c). */
void lr_store_lend(bool on);

/* Before a reset: write out the open record and unmount. A card reset mid-command stays dead until power-off. A card
 * stuck in a write is left as it is after 5 s: the reset goes ahead (APPEND_ERR x 2, -EDEADLK). */
void lr_store_shutdown(void);
/* 0 while the card takes the stream, else why not: -ENODEV no card, -ENOSPC full, -EBUSY lent to USB, or the error
 * that failed it (the one-LED status; the scan response) */
int lr_store_err(void);

#endif
