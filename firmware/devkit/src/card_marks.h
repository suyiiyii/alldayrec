#ifndef CARD_MARKS_H
#define CARD_MARKS_H

#include <stdbool.h>
#include <stdint.h>
#include <string.h>

/* A card FatFs found no volume on (fs_mount -ENODEV, FR_NO_FILESYSTEM) is not always blank: FatFs gives that too for a
 * GPT card (NCS 2.7 has FF_LBA64 0; macOS erases a whole card as GPT by default) and for a FAT or exFAT volume it cannot
 * take (an exFAT root without its bitmap entry, a torn root sector), recordings and all. Only a card whose sector 0
 * shows none of these is blank: zeros, an MBR with no partition, or one of ext4 or NTFS, which FORMAT may take. */

/* A FAT12/16/32 or exFAT boot sector (VBR): the file system's name at its place in the boot sector */
static inline bool lr_fat_vbr(const uint8_t *s)
{
    return !memcmp(s + 3, "EXFAT   ", 8) || !memcmp(s + 82, "FAT32", 5) || !memcmp(s + 54, "FAT", 3);
}

/* Sector 0 (512 B): true if it is a FAT/exFAT VBR or an MBR with a GPT entry (0xEE). Else the MBR's partitions' first
 * sectors in lba[] (*n of them), for the caller to read and try with lr_fat_vbr. */
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
        if (e[4] == 0xEE) {
            return true;
        }
        if (e[4] && start) {
            lba[(*n)++] = start;
        }
    }
    return false;
}

#endif
