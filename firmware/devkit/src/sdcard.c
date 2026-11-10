#include "sdcard.h"
#include "card_marks.h"
#include "lr_log.h"
#include "wdog_facade.h"

#include <ff.h>
#include <hal/nrf_gpio.h>
#include <zephyr/device.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/pinctrl.h>
#include <zephyr/drivers/spi.h>
#include <zephyr/fs/fs.h>
#include <zephyr/fs/fs_sys.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/sd/sd.h>
#include <zephyr/sd/sdmmc.h>
#include <zephyr/storage/disk_access.h>
#include <zephyr/sys/byteorder.h>
#include <zephyr/sys/check.h>

LOG_MODULE_REGISTER(sdcard, CONFIG_LOG_DEFAULT_LEVEL);

static FATFS fat_fs;

/* NCS 2.7's SD stack waits for the card on DISK_IOCTL_CTRL_SYNC, then answers -ENOTSUP: card_ioctl() (zephyr/subsys/sd/
 * sd_ops.c) lacks a break there. FatFs ends a close, an unlink, a mkdir with a sync, so each reported a disk error
 * after the work was done; on a card without LR/ the first boot's mkdir failed and nothing went to the card for a
 * minute. The linker sends the SD disk's calls here (CMakeLists.txt, --wrap); drop it with an NCS that has
 * the break. */
int __real_sdmmc_ioctl(struct sd_card *card, uint8_t cmd, void *buf);
static struct sd_card *disk_card;  // the SD disk's own card, kept by NCS's disk driver: each mount asks it (log_card)
int __wrap_sdmmc_ioctl(struct sd_card *card, uint8_t cmd, void *buf)
{
    disk_card = card;
    int err = __real_sdmmc_ioctl(card, cmd, buf);
    return cmd == DISK_IOCTL_CTRL_SYNC && err == -ENOTSUP ? 0 : err;
}

uint8_t lr_sd_cid[16], lr_sd_csd[16];
uint32_t lr_sd_err[LR_SD_ERR_N];

/* Which card a board's trouble goes with: CID without its serial number, CSD's kind and size */
static void log_card(void)
{
    const uint8_t *c = lr_sd_cid;
    uint32_t sectors = 0;
    disk_access_ioctl("SD", DISK_IOCTL_GET_SECTOR_COUNT, &sectors);
    lr_log(LR_CARD_ID, c[0], sys_get_le32(c + 3), c[7] | c[1] << 8 | c[2] << 16 | (uint32_t) c[8] << 24);
    lr_log(LR_CARD_SIZE, lr_sd_csd[0] >> 6, sectors, (c[13] & 0xF) << 8 | c[14]);
}

static struct fs_mount_t mount_point = {
    .type = FS_FATFS,
    .fs_data = &fat_fs,
};

static bool sd_enabled;

#if DT_NODE_HAS_PROP(DT_PATH(zephyr_user), sd_pwr_gpios)
static const struct gpio_dt_spec sd_pwr = GPIO_DT_SPEC_GET(DT_PATH(zephyr_user), sd_pwr_gpios);

/* The card's SPI lines (SCK MOSI MISO, then CS). Driven, they feed a card whose supply is off, through the module's
 * 10k pull-ups and the card's protection diodes: CS above all, driven high (deselected) from the SPI driver's init on,
 * before main. LR-P3s with a card went flat in under two days "off", and the card never really lost power in
 * a power cycle. */
#define SD_LINE(node, prop, idx) NRF_GET_PIN(DT_PROP_BY_IDX(node, prop, idx)),
static const uint32_t sd_lines[] = {
    DT_FOREACH_PROP_ELEM(DT_CHILD(DT_NODELABEL(custom_spi), group1), psels, SD_LINE)
    NRF_GPIO_PIN_MAP(DT_PROP(DT_GPIO_CTLR_BY_IDX(DT_NODELABEL(spi2), cs_gpios, 0), port),
                     DT_GPIO_PIN_BY_IDX(DT_NODELABEL(spi2), cs_gpios, 0)),
};

void sd_lines_release(void)
{
    for (int i = 0; i < ARRAY_SIZE(sd_lines); i++) {
        nrf_gpio_cfg_default(sd_lines[i]);
    }
}
#endif

// the card's chip select, as the board overlay gives it to the SD SPI bus (LR-X1 D10/P1.15, LR-P1 P0.20)
static const struct gpio_dt_spec sd_cs = GPIO_DT_SPEC_GET_BY_IDX(DT_NODELABEL(spi2), cs_gpios, 0);

// no cs in the config: CS is driven by hand, so it stays high for the wake-up clocks. 400 kHz, as card init wants
static const struct spi_config sd_reset_cfg = {.frequency = 400000,
                                               .operation = SPI_OP_MODE_MASTER | SPI_WORD_SET(8) | SPI_TRANSFER_MSB};

static uint8_t sd_byte(uint8_t tx)
{
    uint8_t rx = 0xFF;
    const struct spi_buf tb = {.buf = &tx, .len = 1}, rb = {.buf = &rx, .len = 1};
    const struct spi_buf_set ts = {.buffers = &tb, .count = 1}, rs = {.buffers = &rb, .count = 1};
    spi_transceive(DEVICE_DT_GET(DT_NODELABEL(spi2)), &sd_reset_cfg, &ts, &rs);
    return rx;
}

static uint8_t sd_cmd(const uint8_t cmd[6], int skip)
{
    for (int i = 0; i < 6; i++) {
        sd_byte(cmd[i]);
    }
    uint8_t r1 = 0xFF;
    for (int i = 0; i < skip + 8 && (i < skip || r1 & 0x80); i++) {
        r1 = sd_byte(0xFF);
    }
    return r1;
}

// A card never powered off (the XIAO's module sits on 3V3) can be left mid-read by a warm reset, streaming data and
// deaf to the CMD0 sd_init starts with. The driver's 80 wake-up clocks do not help: they go out with CS low
// (spi_context ignores SPI_CS_ACTIVE_HIGH for a GPIO CS), and nothing in it sends CMD12. Leaves CS high and the bus
// unlocked, as sdhc_spi expects.
static void sd_spi_reset(void)
{
    static const uint8_t cmd12[6] = {0x4C, 0, 0, 0, 0, 0x61}, cmd0[6] = {0x40, 0, 0, 0, 0, 0x95};
    gpio_pin_configure_dt(&sd_cs, GPIO_OUTPUT_INACTIVE);
    for (int i = 0; i < 10; i++) {
        sd_byte(0xFF);
    }
    gpio_pin_set_dt(&sd_cs, 1);
    uint8_t r12 = sd_cmd(cmd12, 1);  // its response comes after a stuff byte
    for (int i = 0; i < 20000 && sd_byte(0xFF) != 0xFF; i++) {  // busy until the read has stopped; ~0.5 s at most
    }
    uint8_t r0 = sd_cmd(cmd0, 0);
    gpio_pin_set_dt(&sd_cs, 0);
    sd_byte(0xFF);
    LOG_INF("SD reset: CMD12 r1 0x%02x, CMD0 r1 0x%02x", r12, r0);
    lr_log(LR_SD_RESET, 0, r12, r0);
}

#ifdef CONFIG_LR_POWER_DEBUG
void sd_idle_clocks(void)
{
    if (disk_card) {
        lr_sdhc_spi_unlock(disk_card->sdhc);
    }
    gpio_pin_set_dt(&sd_cs, 0);
    sd_byte(0xFF);
    sd_byte(0xFF);
}
#endif

/* A board that switches the card's supply (LR-P3, LR-P1: the nice!nano's VCC on P0.13): off at least 300 ms, long
 * enough to drain, its SPI lines let go meanwhile (sd_lines), then on, so the card starts from reset; the lines back as
 * the SPI driver set them (pinctrl, CS). LR-X1 feeds it from 3V3: nothing to do. */
#if DT_NODE_HAS_PROP(DT_PATH(zephyr_user), sd_pwr_gpios)
static uint32_t lines_cnf[ARRAY_SIZE(sd_lines)], lines_out[ARRAY_SIZE(sd_lines)];
static bool pwr_off;
static int64_t pwr_off_at;
#endif

void sd_power_off(void)
{
#if DT_NODE_HAS_PROP(DT_PATH(zephyr_user), sd_pwr_gpios)
    if (pwr_off) {
        return;
    }
    for (int i = 0; i < ARRAY_SIZE(sd_lines); i++) {
        uint32_t pin = sd_lines[i];
        lines_cnf[i] = nrf_gpio_pin_port_decode(&pin)->PIN_CNF[pin];
        lines_out[i] = nrf_gpio_pin_out_read(sd_lines[i]);
    }
    sd_lines_release();
    gpio_pin_configure_dt(&sd_pwr, GPIO_OUTPUT_INACTIVE);
    pwr_off = true;
    pwr_off_at = k_uptime_get();
#endif
}

static void sd_power_on(void)
{
#if DT_NODE_HAS_PROP(DT_PATH(zephyr_user), sd_pwr_gpios)
    if (!pwr_off) {
        return;
    }
    int64_t left = 300 - (k_uptime_get() - pwr_off_at);
    if (left > 0) {
        k_msleep(left);
    }
    gpio_pin_set_dt(&sd_pwr, 1);
    k_msleep(50);
    for (int i = 0; i < ARRAY_SIZE(sd_lines); i++) {
        uint32_t pin = sd_lines[i];
        nrf_gpio_pin_write(sd_lines[i], lines_out[i]);  // the level first: CS comes back high (deselected)
        nrf_gpio_pin_port_decode(&pin)->PIN_CNF[pin] = lines_cnf[i];
    }
    pwr_off = false;
#endif
}

static void sd_power_cycle(void)
{
    sd_power_off();
    sd_power_on();
}

bool lr_sd_blank, lr_sd_foreign;
uint32_t lr_sd_sectors;

/* After fs_mount's -ENODEV: does the card show a volume all the same (card_marks.h)? Then it is never blank */
static int read_sector(uint32_t lba, uint8_t *s)
{
    return disk_access_read("SD", s, lba, 1);
}

static bool marked(void)
{
    static uint8_t s[512];
    return !lr_card_blank(read_sector, lr_sd_sectors, s);
}

/* disk init, then the mount; what they say of the card kept for TIME's read (sdcard.h). FatFs's FR_NO_FILESYSTEM is
 * fs_mount's -ENODEV (zephyr/subsys/fs/fat_fs.c translate_error; its other -ENODEVs, a bad drive name and no work area,
 * cannot be ours): sector 0 read back fine and FatFs took no FAT or exFAT volume from it. Blank only if the card shows
 * none either (marked: its boot sectors and their backups, the MBR's partitions); a GPT card or a FAT/exFAT volume
 * FatFs cannot mount is foreign. A read that fails is FR_DISK_ERR, -EIO. */
static int mount_try(void)
{
    int err = disk_access_init("SD");
    lr_sd_sectors = 0;
    if (!err) {
        // the disk's own card from here (__wrap_sdmmc_ioctl) for mount_sd_card_reset's sd_init: a card that never
        // mounted (no file system) had none, and a reset left it uninitialised for good, read as -EIO
        disk_access_ioctl("SD", DISK_IOCTL_GET_SECTOR_COUNT, &lr_sd_sectors);
    }
    err = err ?: fs_mount(&mount_point);
    lr_sd_foreign = lr_sd_sectors && err == -ENODEV && marked();
    lr_sd_blank = lr_sd_sectors && err == -ENODEV && !lr_sd_foreign;
    return err;
}

int mount_sd_card(void)
{
    // the card reset at boot only: NCS 2.7's SD disk has no deinit, it would not initialise the card again (mounted back
    // after USB, -EIO); a failed card is reset by mount_sd_card_reset
    static bool booted;
    if (!booted) {
        sd_power_cycle();
        sd_spi_reset();
        booted = true;
    }

    // after a warm reset the card (never powered off) may still be mid-command: give it a few tries
    mount_point.mnt_point = "/SD:";
    mount_point.flags = FS_MOUNT_FLAG_NO_FORMAT;  // a card that reads badly once must not be formatted, recordings and all
    int err = -1;
    for (int i = 0; i < 5 && err; i++) {
        if (i) {
            k_msleep(200 * i);
        }
        watchdog_feed();  // a card that never answers takes seconds per try; the watchdog allows 30 s for all of boot
        err = mount_try();
        LOG_INF("SD mount try %d: %d", i + 1, err);
        lr_log(LR_MOUNT, i + 1, err, 0);
    }
    sd_enabled = !err;
    if (!err) {
        log_card();
    }
    return err;
}

/* One try, no waiting: the store tries a failed card again (lr_store.c retry_card). Logged when it works. */
int mount_sd_card_once(void)
{
    int err = mount_try();
    if (!err) {
        lr_log(LR_MOUNT, 0, 0, 0);
        log_card();
    }
    sd_enabled = !err;
    return err;
}

/* A card that failed and stays failed: BDAE 10-08 11:29 was not back after 10 minutes of mounts, a reboot brought it
 * back. NCS 2.7's disk init returns at once while it holds the disk OK (drivers/disk/sdmmc_subsys.c), so a mount alone
 * goes on reading a card still stuck. What boot does instead: the supply off and on, the SPI reset (SD_RESET), sd_init
 * again on the disk's own card, then the mount; logged as MOUNT x 0 like the plain one, the SD_RESET before it, and
 * when it fails too (a try every minute at most). The store's retry_card, once quick mounts have failed, or for a card
 * not there at boot; its card_up, for a card whose supply phone mode turned off (sd_power_off). */
int mount_sd_card_reset(void)
{
    if (disk_card) {
        lr_sdhc_spi_unlock(disk_card->sdhc);  // a write's busy wait that timed out kept the bus: sd_spi_reset would wait forever
    }
    sd_power_cycle();
    sd_spi_reset();
    int err = disk_card ? sd_init(disk_card->sdhc, disk_card) : 0;  // none: it never answered, disk_access_init inits it
    if (err) {
        lr_sd_sectors = 0;
        lr_sd_blank = lr_sd_foreign = false;
    }
    err = err ?: mount_sd_card_once();
    if (err) {
        lr_log(LR_MOUNT, 0, err, 0);
    }
    return err;
}

/* FORMAT (lr_store.c): the card tried again from scratch first, and formatted only if it still answers and still holds
 * no file system and still has the size the user confirmed (kb, its KB; else -EINVAL: a card swapped in since); one that
 * mounts now, or shows a volume all the same (foreign), is never formatted (-EEXIST), one that does not answer neither
 * (-ENODEV).
 * As the owner agreed (11-08): FAT32 up to 32 GB (SDHC, as the SD spec has it), exFAT above (SDXC). FAT32 because an
 * exFAT file's directory entry set spans two sectors for ~1 in 8 of our files and every f_sync rewrites it whole: cut
 * between the two writes, it is torn and the whole /LR takes no new file (#247, not fixed in main); FAT32's sync
 * rewrites one 32 B entry. The cost: macOS 26 reads a FAT32 volume's whole FAT before it mounts and gives up after 20 s
 * (32 GB at 32 KB clusters: a 3.9 MB FAT, 20-78 s at the 50-190 KB/s the card is read at over USB from LR-X1), so a
 * 32 GB card lent to a Mac may not mount there. Clusters 32 KB up to 32 GB (FatFs picks smaller ones for a card under
 * ~2 GB, too small for FAT32 at 32 KB), 128 KB above, the SD spec's and FatFs's own choice; one FAT; the data area on
 * the card's allocation unit (4 MB, 16 MB above 32 GB: FatFs aligns it on the card's own sector numbers).
 * A format cut short (power, a reset) leaves a blank card, formatted again from the app: the volume shows only once
 * f_mkfs's last write is in. exFAT writes its boot sectors last, so it goes without a partition table (FM_SFD; mounted
 * on macOS, X1 10-18). FAT32 writes its boot sector first and its 3.9 MB FAT after: without a partition table a cut left
 * a FAT32 volume that mounted with half of its FAT the card's old data. So FAT32 goes in an MBR partition (from sector
 * 63; its FAT off the allocation units, the data area on them), whose table FatFs writes last, on a sector 0 zeroed
 * before. work: f_mkfs's buffer, a multiple of 512 B: the FAT is written that much at a time (4 KB: ~1000 writes). */
int format_sd_card(uint32_t kb, void *work, uint32_t len)
{
    unmount_sd_card();
    int err = mount_sd_card_reset();
    if (!lr_sd_blank) {
        return !err || lr_sd_foreign ? -EEXIST : -ENODEV;
    }
    if (lr_sd_sectors / 2 != kb) {
        return -EINVAL;
    }
    // the first 32 sectors zeroed first: sector 0 stays blank until f_mkfs's last write (above), and nothing of an old
    // volume is left there (an exFAT card's backup boot region, 12-23, made macOS take a new FAT32 card for exFAT, 11-10;
    // marked() looks at the backups at 6 and 12)
    memset(work, 0, len);
    for (uint32_t s = 0; s < 32; s += len / 512) {
        if (disk_access_write("SD", work, s, MIN(len / 512, 32 - s))) {
            return -EIO;
        }
    }
    bool sdxc = lr_sd_sectors >= 0x4000000;  // over 32 GB
    MKFS_PARM opt = {.fmt = sdxc ? FM_EXFAT | FM_SFD : FM_FAT32, .n_fat = 1, .align = sdxc ? 32768 : 8192,
                     .au_size = sdxc ? 128 * 1024 : 32 * 1024};
    FRESULT res = f_mkfs("SD:", &opt, work, len);
    if (res == FR_MKFS_ABORTED && !sdxc) {
        opt.au_size = 0;  // too few 32 KB clusters for FAT32: FatFs halves them until it fits
        res = f_mkfs("SD:", &opt, work, len);
    }
    LOG_INF("SD format: %d", res);
    return res ? -EIO : 0;
}

void unmount_sd_card(void)
{
    fs_unmount(&mount_point);
    sd_enabled = false;
}

/* Mount and unmount only: the Omi board's SD enable pin (P0.19) is not routed on LR-X1 or LR-P1. LR-X1 feeds the
 * card from 3V3; LR-P1 from the nice!nano VCC (sd-pwr-gpios), cycled once at boot above. */
void sd_off()
{
    if (sd_enabled) {
        unmount_sd_card();
    }
}

void sd_on()
{
    if (!sd_enabled) {
        mount_sd_card();
    }
}

bool is_sd_on()
{
    return sd_enabled;
}
