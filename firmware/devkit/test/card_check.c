// Host check of what sector 0 says of a card FatFs did not mount (src/card_marks.h): cc -o card_check test/card_check.c
#include <assert.h>
#include <stdio.h>
#include "../src/card_marks.h"

static uint8_t s[512];

static void mbr(int i, uint8_t type, uint32_t start)
{
    uint8_t *e = s + 446 + 16 * i;
    e[4] = type;
    e[8] = start, e[9] = start >> 8, e[10] = start >> 16, e[11] = start >> 24;
    s[510] = 0x55, s[511] = 0xAA;
}

int main(void)
{
    uint32_t lba[4];
    int n;
    assert(!lr_card_marked(s, lba, &n) && n == 0);  // all zeros: blank
    memcpy(s + 3, "EXFAT   ", 8), s[510] = 0x55, s[511] = 0xAA;
    assert(lr_card_marked(s, lba, &n));  // exFAT without a partition table (our own FORMAT, FM_SFD)
    memset(s, 0, sizeof(s)), memcpy(s + 82, "FAT32   ", 8);
    assert(lr_card_marked(s, lba, &n));  // FAT32 VBR
    memset(s, 0, sizeof(s)), memcpy(s + 54, "FAT16   ", 8);
    assert(lr_card_marked(s, lba, &n));  // FAT16 VBR
    memset(s, 0, sizeof(s)), memcpy(s + 3, "NTFS    ", 8), s[510] = 0x55, s[511] = 0xAA;
    assert(!lr_card_marked(s, lba, &n) && n == 0);  // NTFS without a partition table: FORMAT may take it
    memset(s, 0, sizeof(s)), mbr(0, 0xEE, 1);
    assert(lr_card_marked(s, lba, &n));  // GPT (protective MBR): the Mac's default
    memset(s, 0, sizeof(s)), mbr(1, 0xEE, 1);
    assert(lr_card_marked(s, lba, &n));  // a hybrid MBR's GPT entry, not in the first slot
    memset(s, 0, sizeof(s)), mbr(0, 0x0C, 8192), mbr(2, 0x83, 1000000);
    assert(!lr_card_marked(s, lba, &n) && n == 2 && lba[0] == 8192 && lba[1] == 1000000);  // the caller reads these
    memset(s, 0, sizeof(s)), s[510] = 0x55, s[511] = 0xAA;
    assert(!lr_card_marked(s, lba, &n) && n == 0);  // an MBR with no partition (diskutil eraseDisk free): blank
    memset(s, 0, sizeof(s)), mbr(0, 0x0C, 0);
    assert(!lr_card_marked(s, lba, &n) && n == 0);  // an entry starting at 0 is no partition
    puts("card_check ok");
    return 0;
}
