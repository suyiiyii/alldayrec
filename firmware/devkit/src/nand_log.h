#ifndef NAND_LOG_H
#define NAND_LOG_H

#include <stdbool.h>
#include <stdint.h>

/*
 * The record stream's files as a ring log on a bare SPI NAND (W25N01GV / W25N02KV), docs/nand-store.md. Plain C: the
 * chip comes in through nl_ops (nand_card.c on the board, a simulated chip in test/nand_check.c).
 *
 * A page holds one file's bytes; its 16 B header sits where both chips' ECC covers the spare area (4 B at 0x804, 0x814,
 * 0x824, 0x834): seq, file, off, len and a CRC of header and data. Blocks are written in order around the chip, bad
 * ones skipped; the next block is erased as the ring comes to it (the oldest). One caller at a time (the SD mutex).
 */
#define NL_PAGE 2048
#define NL_PPB 64            // pages per block
#define NL_SPARE 56          // spare bytes loaded with a page: 0x800..0x837
#define NL_MAX_BLOCKS 2048   // W25N02KV
#define NL_MAX_FILES 256

struct nl_ops {
    /* page into the chip's cache, then n bytes from column col; *ecc: 0 clean, 1 corrected, 2 not correctable. Tried
     * again on an error; a block whose pages still do not read at mount is kept as it is, unused, never erased */
    int (*read)(uint32_t page, uint16_t col, void *buf, uint16_t n, int *ecc);
    /* a whole page: 2048 B, then the 56 spare bytes from 0x800; -EIO: P-FAIL (and only that: an SPI error is another) */
    int (*program)(uint32_t page, const uint8_t *data, const uint8_t *spare);
    int (*erase)(uint32_t block);  // -EIO: E-FAIL. Other errors (the chip does not answer) are passed up, no block blamed
    int (*mark_bad)(uint32_t block);  // 00h at 0x800 of its first page
};

enum { NL_ECC_FIXED, NL_ECC_BAD, NL_FAILS, NL_TIMEOUTS, NL_LOST, NL_ERR_N };
extern uint32_t nl_err[NL_ERR_N];  // ECC corrected, not correctable or CRC, program/erase failures, busy timeouts, unuploaded files overwritten

/* Scan the chip (nblocks of it) and recover; up: the uploaded pointer kept in NVS (0: none). 0, -EIO (no good block),
 * or the erase's error. The one block it erases is checked first to be older than the newest, or to hold no page */
int nl_mount(const struct nl_ops *ops, uint32_t nblocks, uint32_t up);
/* n bytes onto the end of file: the newest file, or a new one. n, or -EROFS (an older file, or one deleted), -EIO */
int nl_append(uint32_t file, const void *d, uint32_t n);
/* The open page out to the chip, padded (a file closed, unmount) */
int nl_flush(void);
/* Up to n bytes of file at off (one page at most per call): their count, 0 at its end, -ENOENT, -EIO */
int nl_read(uint32_t file, uint32_t off, void *d, uint32_t n);
/* The file off the list (the phone has it): 0, -ENOENT, -EBUSY (the one being written, its open page not flushed). A
 * file deleted is never appended to again (lr_store.c's names only go up). *moved: the uploaded pointer went on,
 * nl_up() to keep */
int nl_delete(uint32_t file, bool *moved);
uint32_t nl_up(void);
/* The list, oldest first: n files; file i's number and size */
int nl_files(void);
void nl_file(int i, uint32_t *file, uint32_t *size);
/* Bytes the good blocks hold, and those of files not deleted */
uint64_t nl_total(void);
uint64_t nl_used(void);

#endif
