// Host check of what a card that did not mount shows (src/card_marks.h): cc -o card_check test/card_check.c
#include <assert.h>
#include <stdio.h>
#include "../src/card_marks.h"

static uint8_t s[512];

static void mbr(uint8_t *b, int i, uint8_t type, uint32_t start)
{
    uint8_t *e = b + 446 + 16 * i;
    e[4] = type;
    e[8] = start, e[9] = start >> 8, e[10] = start >> 16, e[11] = start >> 24;
    b[510] = 0x55, b[511] = 0xAA;
}

// a card of DISK sectors: a few that hold something, the rest zeros; one that fails to read
#define DISK 1000000
static struct { uint32_t lba; uint8_t b[512]; } held[8];
static int n_held;
static uint32_t bad = UINT32_MAX;  // the sector that fails to read (none); past the end reads as zeros here
static uint8_t *put(uint32_t lba)
{
    held[n_held].lba = lba;
    memset(held[n_held].b, 0, 512);
    return held[n_held++].b;
}
static void vbr(uint32_t lba, const char *name, int at) { memcpy(put(lba) + at, name, strlen(name)); }
static int disk_read(uint32_t lba, uint8_t *b)
{
    if (lba == bad) {
        return -5;
    }
    memset(b, 0, 512);
    for (int i = 0; i < n_held; i++) {
        if (held[i].lba == lba) {
            memcpy(b, held[i].b, 512);
        }
    }
    return 0;
}
static bool blank(void)
{
    uint8_t b[512];
    bool r = lr_card_blank(disk_read, DISK, b);
    n_held = 0, bad = UINT32_MAX;  // the next card
    return r;
}

int main(void)
{
    uint32_t lba[4];
    int n;
    // sector 0 alone
    assert(!lr_card_marked(s, lba, &n) && n == 0);  // all zeros: blank
    memcpy(s + 3, "EXFAT   ", 8), s[510] = 0x55, s[511] = 0xAA;
    assert(lr_card_marked(s, lba, &n));  // exFAT without a partition table (our own FORMAT, FM_SFD)
    memset(s, 0, sizeof(s)), memcpy(s + 82, "FAT32   ", 8);
    assert(lr_card_marked(s, lba, &n));  // FAT32 VBR
    memset(s, 0, sizeof(s)), memcpy(s + 54, "FAT16   ", 8);
    assert(lr_card_marked(s, lba, &n));  // FAT16 VBR
    memset(s, 0, sizeof(s)), memcpy(s + 3, "NTFS    ", 8), s[510] = 0x55, s[511] = 0xAA;
    assert(!lr_card_marked(s, lba, &n) && n == 0);  // NTFS without a partition table: FORMAT may take it
    memset(s, 0, sizeof(s)), mbr(s, 0, 0xEE, 1);
    assert(lr_card_marked(s, lba, &n));  // GPT (protective MBR): the Mac's default
    memset(s, 0, sizeof(s)), mbr(s, 1, 0xEE, 1);
    assert(lr_card_marked(s, lba, &n));  // a hybrid MBR's GPT entry, not in the first slot
    for (const uint8_t *t = (const uint8_t[]){0x01, 0x04, 0x06, 0x0B, 0x0C, 0x0E, 0xEF, 0}; *t; t++) {
        memset(s, 0, sizeof(s)), mbr(s, 2, *t, 8192);
        assert(lr_card_marked(s, lba, &n));  // a FAT partition, its boot sector there or not: its volume may be
    }
    memset(s, 0, sizeof(s)), mbr(s, 0, 0x07, 8192), mbr(s, 2, 0x83, 900000);
    assert(!lr_card_marked(s, lba, &n) && n == 2 && lba[0] == 8192 && lba[1] == 900000);  // NTFS/exFAT, Linux: to look at
    memset(s, 0, sizeof(s)), s[510] = 0x55, s[511] = 0xAA;
    assert(!lr_card_marked(s, lba, &n) && n == 0);  // an MBR with no partition (diskutil eraseDisk free): blank
    memset(s, 0, sizeof(s)), mbr(s, 0, 0x07, 0);
    assert(!lr_card_marked(s, lba, &n) && n == 0);  // an entry starting at 0 is no partition
    memset(s, 0, sizeof(s)), mbr(s, 1, 0, 8192);
    assert(!lr_card_marked(s, lba, &n) && n == 0);  // nor is one of type 0, whatever its start says
    memset(s, 0, sizeof(s)), mbr(s, 0, 0xEE, 1), mbr(s, 1, 0x07, 8192), s[510] = 0;
    assert(!lr_card_marked(s, lba, &n) && n == 0);  // no 55AA: not an MBR, its "entries" are noise

    // the whole card
    assert(blank());                                                 // zeros
    vbr(12, "EXFAT   ", 3);
    assert(!blank());  // sector 0 gone, exFAT's backup boot sector (12) there
    vbr(6, "FAT32   ", 82);
    assert(!blank());  // FAT32's backup (6)
    mbr(put(0), 0, 0x07, 2048), vbr(2048, "NTFS    ", 3);
    assert(blank());  // an NTFS partition: FORMAT may take it
    mbr(put(0), 0, 0x07, 2048), vbr(2048, "EXFAT   ", 3);
    assert(!blank());  // exFAT in an 0x07 partition
    mbr(put(0), 0, 0x07, 2048), vbr(2048 + 12, "EXFAT   ", 3);
    assert(!blank());  // its boot sector gone, its backup there
    mbr(put(0), 0, 0x83, 2048), vbr(2048 + 6, "FAT32   ", 82);
    assert(!blank());  // FAT32's backup in a partition typed otherwise
    mbr(put(0), 0, 0x07, DISK);
    assert(!blank());  // a partition past the card's end
    bad = 0;
    assert(!blank());  // sector 0 cannot be read
    bad = 12;
    assert(!blank());  // nor a backup: nothing is formatted on a guess
    vbr(63, "FAT32   ", 82), vbr(69, "FAT32   ", 82);
    assert(blank());  // our FAT32 FORMAT cut short: its boot sectors in, the MBR (its last write) not yet
    puts("card_check ok");
    return 0;
}
