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
/* The card's four SPI lines let go (high impedance), on a board that switches the card's supply: before its supply goes
 * off for good (System OFF, lr_power.c) */
void sd_lines_release(void);
/* The card's supply off (unmounted first), its lines let go and kept as they were for mount_sd_card_reset, which powers
 * it on again; nothing on a board that cannot switch it */
void sd_power_off(void);

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
