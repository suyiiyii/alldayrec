#ifndef CARD_MARKS_H
#define CARD_MARKS_H

#include <stdbool.h>
#include <stdint.h>
#include <string.h>

/* A card FatFs found no volume on (fs_mount -ENODEV, FR_NO_FILESYSTEM) is not always blank: FatFs gives that too for a
 * GPT card (NCS 2.7 has FF_LBA64 0; macOS erases a whole card as GPT by default) and for a FAT or exFAT volume it cannot
 * take (an exFAT root without its bitmap entry, a torn root sector, a boot sector gone but its backup there), recordings
 * and all. Only a card that shows none of these is blank: zeros, an MBR with no partition or only NTFS/exFAT (0x07) and
 * Linux (0x83) ones and no FAT/exFAT volume in them, FAT partitions whose volume's start is wiped, a GPT with no
 * partition in it, or ext4 or NTFS without partitions, which FORMAT may take. Not seen (blank): a partitioned card whose
 * MBR is gone (no table, no volume start), a FAT16 card without partitions whose boot sector is gone (FAT16 has no
 * backup). */

/* A FAT12/16/32 or exFAT boot sector (VBR): the file system's name at its place in the boot sector */
static inline bool lr_fat_vbr(const uint8_t *s)
{
    return !memcmp(s + 3, "EXFAT   ", 8) || !memcmp(s + 82, "FAT32", 5) || !memcmp(s + 54, "FAT", 3);
}

/* MBR partition types of FAT volumes: FAT12, FAT16 (<32 MB), FAT16B, FAT32 (CHS, LBA), FAT16 (LBA) */
static inline bool lr_fat_type(uint8_t t)
{
    return t == 0x01 || t == 0x04 || t == 0x06 || t == 0x0B || t == 0x0C || t == 0x0E;
}

/* A FAT partition whose first LR_FAT_WIPED sectors all hold one byte, 0x00 or 0xFF, has no volume left to mount: a
 * computer's erase cut short (diskutil eraseDisk wipes the old volumes before it writes the new table) leaves the old
 * table and a wiped volume. 33: the boot sector, FSInfo, their backups at 6 and 7, and the first FAT sector of a FAT32
 * volume (32 reserved sectors: FatFs, Windows, macOS; FAT12/16 have theirs at 1): a volume with any of these left,
 * its files findable, is not blank. */
#define LR_FAT_WIPED 33

static inline bool lr_wiped(const uint8_t *s)
{
    for (int i = 1; i < 512; i++) {
        if (s[i] != s[0]) {
            return false;
        }
    }
    return s[0] == 0x00 || s[0] == 0xFF;
}

/* What sector 0's MBR leaves to look at: its partitions' first sectors, FAT ones to be found wiped (fat[i]), and
 * whether it is a GPT's protective MBR (0xEE, a hybrid MBR's too) */
struct lr_parts {
    int n;
    uint32_t lba[4];
    bool fat[4];
    bool gpt;
};

/* Sector 0 (512 B): true if it is a FAT/exFAT VBR, or an MBR with a partition that is not NTFS/exFAT (0x07), Linux
 * (0x83), FAT or GPT's 0xEE: an extended partition (0x05, 0x0F, 0x85: FatFs reads no logical one, a FAT volume and its
 * files may be in it), hidden FAT, HFS+, any other. Else the partitions to look at in p, for the caller (0x07 is NTFS and
 * exFAT alike). */
static inline bool lr_card_marked(const uint8_t *s0, struct lr_parts *p)
{
    memset(p, 0, sizeof(*p));
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
            p->gpt = true;
        } else if (e[4] && e[4] != 0x07 && e[4] != 0x83 && !lr_fat_type(e[4])) {
            return true;
        } else if (e[4] && start) {
            p->fat[p->n] = lr_fat_type(e[4]);
            p->lba[p->n++] = start;
        }
    }
    return false;
}

static inline uint32_t lr_le32(const uint8_t *b)
{
    return b[0] | b[1] << 8 | b[2] << 16 | (uint32_t) b[3] << 24;
}

/* The GPT header at lba: -1 none there, 0 its partition entries all unused, 1 one in use (or a header or entry that
 * cannot be read or makes no sense: nothing is formatted on a guess). s is overwritten. */
static inline int lr_gpt_used(int (*read)(uint32_t lba, uint8_t *s), uint32_t sectors, uint32_t lba, uint8_t *s)
{
    if (lba >= sectors || read(lba, s)) {
        return 1;
    }
    if (memcmp(s, "EFI PART", 8)) {
        return -1;
    }
    uint32_t at = lr_le32(s + 72), num = lr_le32(s + 80), size = lr_le32(s + 84);
    if (lr_le32(s + 76) || size < 128 || size > 512 || size % 128 || num > 512 / size * 32) {
        return 1;  // entries past 2 TB, an entry size the spec does not allow, more than 32 sectors of them
    }
    for (uint32_t i = 0; i < num; i++) {
        uint32_t byte = i * size;
        if (byte % 512 == 0 && (at + byte / 512 >= sectors || read(at + byte / 512, s))) {
            return 1;
        }
        for (int k = 0; k < 16; k++) {
            if (s[byte % 512 + k]) {
                return 1;  // a partition type GUID: a partition
            }
        }
    }
    return 0;
}

/* The whole look at a card that did not mount: sector 0, then at each volume's start (sector 0, each MBR partition) its
 * boot sector and the backups (FAT32 at +6, exFAT at +12), a FAT partition's first LR_FAT_WIPED sectors, and a GPT's
 * header (primary at 1, or the backup in the last sector) and its entries. read(lba, s) fills s (512 B), 0 or an
 * error; a sector that cannot be read, or one past the card's end, counts as marked: nothing is formatted on a guess.
 * A GPT with no header left (its protective MBR alone) is blank, like a partitioned card whose MBR is gone. */
static inline bool lr_card_blank(int (*read)(uint32_t lba, uint8_t *s), uint32_t sectors, uint8_t *s)
{
    struct lr_parts p;
    if (read(0, s) || lr_card_marked(s, &p)) {
        return false;
    }
    if (p.gpt) {
        int used = lr_gpt_used(read, sectors, 1, s);
        if (used < 0) {
            used = lr_gpt_used(read, sectors, sectors - 1, s);
        }
        if (used > 0) {
            return false;
        }
    }
    static const uint8_t off[] = {0, 6, 12};
    for (int i = -1; i < p.n; i++) {  // -1: sector 0's own volume
        bool fat = i >= 0 && p.fat[i];
        uint32_t base = i < 0 ? 0 : p.lba[i];
        for (int j = 0; j < (fat ? LR_FAT_WIPED : 3); j++) {
            uint32_t lba = base + (fat ? j : off[j]);
            if (lba == 0) {
                continue;  // sector 0 itself is looked at already
            }
            if (lba >= sectors || read(lba, s) || (fat ? !lr_wiped(s) : lr_fat_vbr(s))) {
                return false;
            }
        }
    }
    return true;
}

#endif
