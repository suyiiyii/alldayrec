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
static uint32_t ff_from, ff_to;    // sectors that read as all 0xFF (an erased flash block): none
static uint8_t *put(uint32_t lba)
{
    held[n_held].lba = lba;
    memset(held[n_held].b, 0, 512);
    return held[n_held++].b;
}
// a GPT header at lba: num entries of size B from entries_at (the rest of the header unused here)
static void gpt(uint32_t lba, uint32_t entries_at, uint32_t num, uint32_t size)
{
    uint8_t *h = put(lba);
    memcpy(h, "EFI PART", 8);
    for (int i = 0; i < 4; i++) {
        h[72 + i] = entries_at >> 8 * i, h[80 + i] = num >> 8 * i, h[84 + i] = size >> 8 * i;
    }
}
static void vbr(uint32_t lba, const char *name, int at) { memcpy(put(lba) + at, name, strlen(name)); }
static int disk_read(uint32_t lba, uint8_t *b)
{
    if (lba == bad) {
        return -5;
    }
    memset(b, lba >= ff_from && lba < ff_to ? 0xFF : 0, 512);
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
    n_held = 0, bad = UINT32_MAX, ff_from = ff_to = 0;  // the next card
    return r;
}

int main(void)
{
    struct lr_parts p;
    // sector 0 alone
    assert(!lr_card_marked(s, &p) && p.n == 0);  // all zeros: blank
    memcpy(s + 3, "EXFAT   ", 8), s[510] = 0x55, s[511] = 0xAA;
    assert(lr_card_marked(s, &p));  // exFAT without a partition table (our own FORMAT, FM_SFD)
    memset(s, 0, sizeof(s)), memcpy(s + 82, "FAT32   ", 8);
    assert(lr_card_marked(s, &p));  // FAT32 VBR
    memset(s, 0, sizeof(s)), memcpy(s + 54, "FAT16   ", 8);
    assert(lr_card_marked(s, &p));  // FAT16 VBR
    memset(s, 0, sizeof(s)), memcpy(s + 3, "NTFS    ", 8), s[510] = 0x55, s[511] = 0xAA;
    assert(!lr_card_marked(s, &p) && p.n == 0);  // NTFS without a partition table: FORMAT may take it
    memset(s, 0, sizeof(s)), mbr(s, 0, 0xEE, 1);
    assert(!lr_card_marked(s, &p) && p.gpt && p.n == 0);  // GPT (protective MBR, the Mac's default): its table to look at
    memset(s, 0, sizeof(s)), mbr(s, 1, 0xEE, 1);
    assert(!lr_card_marked(s, &p) && p.gpt);  // a hybrid MBR's GPT entry, not in the first slot
    for (const uint8_t *t = (const uint8_t[]){0x01, 0x04, 0x06, 0x0B, 0x0C, 0x0E, 0}; *t; t++) {
        memset(s, 0, sizeof(s)), mbr(s, 2, *t, 8192);
        assert(!lr_card_marked(s, &p) && p.n == 1 && p.fat[0] && p.lba[0] == 8192);  // a FAT partition: its start to look at
    }
    for (const uint8_t *t = (const uint8_t[]){0xEF, 0x05, 0x0F, 0x85, 0x1C, 0xAF, 0xA5, 0}; *t; t++) {
        memset(s, 0, sizeof(s)), mbr(s, 1, *t, 2048);
        assert(lr_card_marked(s, &p));  // extended (logical volumes FatFs does not read), hidden FAT, HFS+, BSD
    }
    memset(s, 0, sizeof(s)), mbr(s, 0, 0x07, 8192), mbr(s, 2, 0x83, 900000);
    assert(!lr_card_marked(s, &p) && p.n == 2 && p.lba[0] == 8192 && p.lba[1] == 900000 && !p.fat[0] && !p.fat[1]);  // NTFS/exFAT, Linux: to look at
    memset(s, 0, sizeof(s)), s[510] = 0x55, s[511] = 0xAA;
    assert(!lr_card_marked(s, &p) && p.n == 0);  // an MBR with no partition (diskutil eraseDisk free): blank
    memset(s, 0, sizeof(s)), mbr(s, 0, 0x07, 0);
    assert(!lr_card_marked(s, &p) && p.n == 0);  // an entry starting at 0 is no partition
    memset(s, 0, sizeof(s)), mbr(s, 1, 0, 8192);
    assert(!lr_card_marked(s, &p) && p.n == 0);  // nor is one of type 0, whatever its start says
    memset(s, 0, sizeof(s)), mbr(s, 0, 0xEE, 1), mbr(s, 1, 0x07, 8192), s[510] = 0;
    assert(!lr_card_marked(s, &p) && p.n == 0);  // no 55AA: not an MBR, its "entries" are noise

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
    mbr(put(0), 0, 0x0F, 2048), mbr(put(2048), 0, 0x0C, 2048), vbr(4096, "FAT32   ", 82), vbr(4102, "FAT32   ", 82);
    assert(!blank());  // a whole FAT32 volume in a logical partition (EBR at 2048): files and all
    uint8_t *m = put(0);
    for (int i = 0; i < 4; i++) {
        mbr(m, i, i & 1 ? 0x83 : 0x07, 2048 + i * 100000), vbr(2048 + i * 100000, "NTFS    ", 3);
    }
    assert(blank());  // four NTFS and Linux partitions: FORMAT may take them
    bad = 0;
    assert(!blank());  // sector 0 cannot be read
    bad = 12;
    assert(!blank());  // nor a backup: nothing is formatted on a guess
    vbr(63, "FAT32   ", 82), vbr(69, "FAT32   ", 82);
    assert(blank());  // our FAT32 FORMAT cut short: its boot sectors in, the MBR (its last write) not yet

    // a computer's erase cut short: the old MBR's FAT partition left, its volume's start wiped (zeros or 0xFF)
    mbr(put(0), 0, 0x0C, 63);
    assert(blank());  // FAT32 at 63 (our FORMAT's place), sectors 63-95 zeros
    mbr(put(0), 0, 0x0C, 63), ff_from = 63, ff_to = 63 + LR_FAT_WIPED;
    assert(blank());  // the same, wiped to 0xFF
    mbr(put(0), 0, 0x0C, 63), ff_from = 70, ff_to = 63 + LR_FAT_WIPED;
    assert(blank());  // zeros, then 0xFF: each sector one byte throughout
    mbr(put(0), 0, 0x0C, 63), vbr(63, "FAT32   ", 82), vbr(69, "FAT32   ", 82);
    assert(!blank());  // a whole FAT32 volume FatFs did not take: recordings and all
    mbr(put(0), 0, 0x0C, 63), vbr(69, "FAT32   ", 82);
    assert(!blank());  // its boot sector gone, the backup there
    mbr(put(0), 0, 0x0C, 63), memcpy(put(64), "RRaA", 4);
    assert(!blank());  // FSInfo left
    mbr(put(0), 0, 0x0C, 63), memcpy(put(63 + 32), "\xF8\xFF\xFF\x0F", 4);
    assert(!blank());  // the FAT's first sector left: its files findable
    mbr(put(0), 0, 0x0E, 2048), memcpy(put(2049), "\xF8\xFF\xFF\xFF", 4);
    assert(!blank());  // FAT16 (LBA), its FAT at 1
    mbr(put(0), 0, 0x0C, 63), put(63 + LR_FAT_WIPED - 1)[511] = 1;
    assert(!blank());  // one byte left in the last sector looked at
    mbr(put(0), 0, 0x0C, 63), memset(put(66), 0x5A, 512);
    assert(!blank());  // a sector of one byte, but not an erased one
    mbr(put(0), 0, 0x0C, 63), bad = 80;
    assert(!blank());  // a sector that cannot be read
    mbr(put(0), 0, 0x0C, DISK - 10);
    assert(!blank());  // a partition running past the card's end
    mbr(put(0), 0, 0x0C, 63), mbr(held[0].b, 1, 0xAF, 900000);
    assert(!blank());  // a wiped FAT partition beside an HFS+ one

    // GPT: its protective MBR, the header at 1 (or the backup in the last sector), 128 entries of 128 B at 2
    mbr(put(0), 0, 0xEE, 1), gpt(1, 2, 128, 128);
    assert(blank());  // a GPT with no partition (all entries unused)
    mbr(put(0), 0, 0xEE, 1), gpt(1, 2, 128, 128), put(2)[0] = 0xA2;
    assert(!blank());  // one partition (Basic data's type GUID starts A2): the Mac's exFAT, the user's files
    mbr(put(0), 0, 0xEE, 1), gpt(1, 2, 128, 128), put(2 + 31)[384 + 15] = 1;
    assert(!blank());  // the last of the 128 entries in use
    mbr(put(0), 0, 0xEE, 1), gpt(DISK - 1, DISK - 33, 128, 128), put(DISK - 33)[0] = 0x28;
    assert(!blank());  // the primary header gone, the backup's one partition (EFI system) there
    mbr(put(0), 0, 0xEE, 1), gpt(DISK - 1, DISK - 33, 128, 128);
    assert(blank());  // the primary header gone, the backup's table empty
    mbr(put(0), 0, 0xEE, 1);
    assert(blank());  // the protective MBR alone: no table left to hold a partition
    mbr(put(0), 0, 0xEE, 1), gpt(1, 2, 64, 192);
    assert(!blank());  // an entry size the spec does not allow
    mbr(put(0), 0, 0xEE, 1), gpt(1, 2, 128, 128), bad = 17;
    assert(!blank());  // an entry sector that cannot be read
    mbr(put(0), 0, 0xEE, 1), gpt(1, 2, 128, 128), mbr(held[0].b, 1, 0x0C, 409640), vbr(409640, "FAT32   ", 82);
    assert(!blank());  // a hybrid MBR's whole FAT32 volume
    puts("card_check ok");
    return 0;
}
