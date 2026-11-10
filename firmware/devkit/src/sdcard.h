#ifndef SDCARD_H
#define SDCARD_H

#include <stdbool.h>
#include <stdint.h>

/**
 * @brief Mount the SD Card (FAT, formatted beforehand) at /SD:
 *
 * @return 0 if successful, negative errno code if error
 */
int mount_sd_card(void);
int mount_sd_card_once(void);
/* A failed card from scratch, as at boot: supply off and on where the board can, SPI reset, sd_init, mount */
int mount_sd_card_reset(void);
/* sdhc_spi.c: the SD driver's bus lock, left held by a busy wait that timed out, let go (before mount_sd_card_reset's
 * own transfers) */
struct device;
void lr_sdhc_spi_unlock(const struct device *sdhc);
void unmount_sd_card(void);
/* The last mount try's card: its 512 B sectors if it answered (0: none did: no card, or not seated), whether it held
 * no FAT or exFAT volume (blank, ext4, NTFS: FORMAT takes it), and whether it did not mount but shows one all the same
 * (foreign: GPT, a FAT/exFAT volume FatFs cannot take; never formatted, a computer's job) */
extern bool lr_sd_blank, lr_sd_foreign;
extern uint32_t lr_sd_sectors;
/* A card with no file system made exFAT (FORMAT, lr_store.c), if it is still kb KB; work a buffer for f_mkfs: 0, -EEXIST
 * it mounts (never formatted), -ENODEV it no longer answers, -EINVAL another size, -EIO the format failed. Unmounted
 * after; SD mutex held. */
int format_sd_card(uint32_t kb, void *work, uint32_t len);
/* The card's four SPI lines let go (high impedance), on a board that switches the card's supply: before its supply goes
 * off for good (System OFF, lr_power.c) */
void sd_lines_release(void);
/* The card's supply off (unmounted first), its lines let go and kept as they were for mount_sd_card_reset, which powers
 * it on again; nothing on a board that cannot switch it */
void sd_power_off(void);
/* Power tests (CONFIG_LR_POWER_DEBUG): CS high and 16 clocks, the card deselected the way the spec ends a transaction */
void sd_idle_clocks(void);

void sd_on();
void sd_off();

bool is_sd_on();

/* The card's CID and CSD as its last init read them (sdhc_spi.c takes CMD10's and CMD9's data blocks): CARD_ID and
 * CARD_SIZE at each mount */
extern uint8_t lr_sd_cid[16], lr_sd_csd[16];
/* The SD driver's failed tries of data transfers (sdhc_spi.c), read and cleared with SD_WRITES (lr_store.c), both under
 * the SD mutex: SD_ERRORS */
enum { LR_SD_CRC, LR_SD_TIMEOUT, LR_SD_BUSY, LR_SD_OTHER, LR_SD_RETRIED, LR_SD_ERR_N };
extern uint32_t lr_sd_err[LR_SD_ERR_N];
#endif
