#ifndef CARD_MARKS_H
#define CARD_MARKS_H

#include <stdbool.h>
#include <stdint.h>
#include <string.h>

/* A card FatFs found no volume on (fs_mount -ENODEV, FR_NO_FILESYSTEM) is not always blank: FatFs gives that too for a
 * GPT card (NCS 2.7 has FF_LBA64 0; macOS erases a whole card as GPT by default) and for a FAT or exFAT volume it cannot
 * take (an exFAT root without its bitmap entry, a torn root sector, a boot sector gone but its backup there), recordings
 * and all. Only a card that shows none of these is blank: zeros, an MBR with no partition or only NTFS/exFAT (0x07) and
 * Linux (0x83) ones and no FAT/exFAT volume in them, or ext4 or NTFS without partitions, which FORMAT may take. Not seen
 * (blank): a partitioned card whose MBR is gone (no table, no volume start), a FAT16 card without partitions whose boot
 * sector is gone (FAT16 has no backup). */

/* A FAT12/16/32 or exFAT boot sector (VBR): the file system's name at its place in the boot sector */
static inline bool lr_fat_vbr(const uint8_t *s)
{
    return !memcmp(s + 3, "EXFAT   ", 8) || !memcmp(s + 82, "FAT32", 5) || !memcmp(s + 54, "FAT", 3);
}

/* Sector 0 (512 B): true if it is a FAT/exFAT VBR, or an MBR with any partition but NTFS/exFAT (0x07) and Linux (0x83):
 * FAT (its volume may be there without its boot sector), GPT (0xEE), an extended partition (0x05, 0x0F, 0x85: FatFs reads
 * no logical one, a FAT volume and its files may be in it), hidden FAT, HFS+, any other. Else the 0x07 and 0x83
 * partitions' first sectors in lba[] (*n of them: 0x07 is NTFS and exFAT alike), for the caller to look at. */
static inline bool lr_card_marked(const uint8_t *s0, uint32_t lba[4], int *n)
{
    *n = 0;
    if (lr_fat_vbr(s0)) {
        return true;
    }
    if (s0[510] != 0x55 || s0[511] != 0xAA) {
        return false;  // no MBR either: zeros, ext4 (its superblock is at 1 KB), random data
    }
    for (int i = 0; i < 4; i++) {
        const uint8_t *e = s0 + 446 + 16 * i;
        uint32_t start = e[8] | e[9] << 8 | e[10] << 16 | (uint32_t) e[11] << 24;
        if (e[4] && e[4] != 0x07 && e[4] != 0x83) {
            return true;
        }
        if (e[4] && start) {
            lba[(*n)++] = start;
        }
    }
    return false;
}

/* The whole look at a card that did not mount: sector 0, then at each volume's start (sector 0, each MBR partition) its
 * boot sector and the backups (FAT32 at +6, exFAT at +12). read(lba, s) fills s (512 B), 0 or an error; a sector that
 * cannot be read, or one past the card's end, counts as marked: nothing is formatted on a guess. */
static inline bool lr_card_blank(int (*read)(uint32_t lba, uint8_t *s), uint32_t sectors, uint8_t *s)
{
    uint32_t base[5] = {0};
    int n;
    if (read(0, s) || lr_card_marked(s, base + 1, &n)) {
        return false;
    }
    static const uint8_t off[] = {0, 6, 12};
    for (int i = 0; i <= n; i++) {
        for (int j = i ? 0 : 1; j < 3; j++) {  // sector 0 itself is looked at already
            uint32_t lba = base[i] + off[j];
            if (lba >= sectors || read(lba, s) || lr_fat_vbr(s)) {
                return false;
            }
        }
    }
    return true;
}

#endif
