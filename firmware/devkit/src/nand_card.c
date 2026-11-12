/* CONFIG_LR_NAND: a bare W25N01GV / W25N02KV on SPIM3 in place of the SD card (docs/nand-store.md). The chip's commands,
 * the ring log (nand_log.c) as a file system at /SD: with the SD card's paths, and sdcard.h for lr_store.c, which reads
 * and writes its files as it does on a card. Callers hold the SD mutex (write_sdcard_mutex), as for the card. */
#include "sdcard.h"
#include "lr_log.h"
#include "nand_log.h"

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <hal/nrf_gpio.h>
#include <zephyr/device.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/pinctrl.h>
#include <zephyr/drivers/spi.h>
#include <zephyr/fs/fs.h>
#include <zephyr/fs/fs_sys.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/settings/settings.h>
#include <zephyr/sys/byteorder.h>

LOG_MODULE_REGISTER(nand, CONFIG_LOG_DEFAULT_LEVEL);

#define NAND_SPI DT_NODELABEL(spi3)
static const struct device *spi = DEVICE_DT_GET(NAND_SPI);
static const struct spi_config cfg = {
    .frequency = CONFIG_LR_NAND_SPI_HZ,
    .operation = SPI_OP_MODE_MASTER | SPI_WORD_SET(8) | SPI_TRANSFER_MSB,  // mode 0
    .cs = {.gpio = GPIO_DT_SPEC_GET_BY_IDX(NAND_SPI, cs_gpios, 0)},
};

bool lr_sd_blank, lr_sd_foreign;  // never: a NAND has no file system to be missing
uint32_t lr_sd_sectors;
uint8_t lr_sd_cid[16], lr_sd_csd[16];
uint32_t lr_sd_err[LR_SD_ERR_N];

static uint8_t chip;  // the JEDEC ID's last byte: 0x21 W25N01GV, 0x22 W25N02KV
static bool dpd, mounted;

/* One command: cmd, then tx and tx2 out, or rx in, CS low throughout */
static int xfer(const uint8_t *cmd, size_t clen, const void *tx, size_t tlen, const void *tx2, size_t t2len, void *rx,
                size_t rlen)
{
    const struct spi_buf t[3] = {{.buf = (void *) cmd, .len = clen}, {.buf = (void *) tx, .len = tlen},
                                 {.buf = (void *) tx2, .len = t2len}};
    const struct spi_buf r[2] = {{.buf = NULL, .len = clen}, {.buf = rx, .len = rlen}};
    const struct spi_buf_set ts = {t, tx2 ? 3 : tx ? 2 : 1}, rs = {r, 2};
    int err = rx ? spi_transceive(spi, &cfg, &ts, &rs) : spi_write(spi, &cfg, &ts);
    return err ? -ECOMM : 0;  // not -EIO (the SPI driver's own): that one is the chip's P-FAIL / E-FAIL, a bad block
}

static int op(uint8_t c)
{
    return xfer(&c, 1, NULL, 0, NULL, 0, NULL, 0);
}

/* W25N02KV out of deep power-down (ABh, tRES 1.5 ms) before anything else; W25N01GV has none */
static void wake(void)
{
    if (dpd) {
        op(0xAB);
        k_usleep(1500);
        dpd = false;
    }
}

/* Between bursts (a sync, a close, a read): W25N02KV into deep power-down, 1 uA instead of 10-50 uA standby */
static void doze(void)
{
    if (chip == 0x22 && !dpd && !op(0xB9)) {
        dpd = true;
    }
}

static int get_reg(uint8_t reg, uint8_t *v)
{
    const uint8_t c[2] = {0x0F, reg};
    return xfer(c, 2, NULL, 0, NULL, 0, v, 1);
}

static int set_reg(uint8_t reg, uint8_t v)
{
    const uint8_t c[3] = {0x1F, reg, v};
    return xfer(c, 3, NULL, 0, NULL, 0, NULL, 0);
}

/* BUSY clear within ms (tRD 60 us, tPP 0.7 ms, tBE 10 ms at most); *sr: status register 3 */
static int ready(uint8_t *sr, int ms)
{
    int64_t t0 = k_uptime_get();
    for (;;) {
        int err = get_reg(0xC0, sr);
        if (err || !(*sr & 1)) {
            return err;
        }
        if (k_uptime_get() - t0 > ms) {
            nl_err[NL_TIMEOUTS]++;
            return -ETIMEDOUT;
        }
        k_busy_wait(20);
    }
}

static int page_cmd(uint8_t c, uint32_t page)
{
    const uint8_t b[4] = {c, page >> 16, page >> 8, page};
    return xfer(b, 4, NULL, 0, NULL, 0, NULL, 0);
}

static int n_read(uint32_t page, uint16_t col, void *buf, uint16_t n, int *ecc)
{
    uint8_t sr;
    wake();
    int err = page_cmd(0x13, page) ?: ready(&sr, 2);
    if (err) {
        return err;
    }
    // ECC-1/0: 01 corrected; W25N02KV's 11 corrected past its bit-flip threshold; 10 (and W25N01GV's 11) not
    uint8_t e = sr >> 4 & 3;
    *ecc = !e ? 0 : e == 1 || (e == 3 && chip == 0x22) ? 1 : 2;
    const uint8_t c[4] = {0x03, col >> 8, col, 0};
    return xfer(c, 4, NULL, 0, NULL, 0, buf, n);
}

static int n_program(uint32_t page, const uint8_t *data, const uint8_t *spare)
{
    static const uint8_t load[3] = {0x02, 0, 0};  // load from column 0, the rest of the buffer FFh
    uint8_t sr;
    wake();
    int err = op(0x06) ?: xfer(load, 3, data, NL_PAGE, spare, NL_SPARE, NULL, 0) ?: page_cmd(0x10, page) ?: ready(&sr, 5);
    return err ?: sr & 0x08 ? -EIO : 0;  // P-FAIL
}

static int n_erase(uint32_t block)
{
    uint8_t sr;
    wake();
    int err = op(0x06) ?: page_cmd(0xD8, block * NL_PPB) ?: ready(&sr, 30);
    return err ?: sr & 0x04 ? -EIO : 0;  // E-FAIL
}

/* 00h at 0x800 of the block's first page, with ECC off for it (as Linux's spinand writes the mark raw): the page may
 * be programmed already, and with ECC on a second program writes parity over the old */
static int n_mark_bad(uint32_t block)
{
    static const uint8_t load[3] = {0x02, NL_PAGE >> 8, 0}, zero = 0;
    uint8_t sr, cfg2;
    wake();
    int err = get_reg(0xB0, &cfg2);
    if (err) {
        return err;  // cfg2 not known: nothing written back (OTP-E, BUF)
    }
    err = set_reg(0xB0, cfg2 & ~0x10) ?: op(0x06) ?: xfer(load, 3, &zero, 1, NULL, 0, NULL, 0) ?:
          page_cmd(0x10, block * NL_PPB) ?: ready(&sr, 5);
    int on = set_reg(0xB0, cfg2 | 0x10);
    return err ?: on;
}

/* In NVS (nand_log.h): the seq ceiling, written as the log's seqs reach it (about every 8 MB); the block an erase is
 * begun on, written before each erase (every 128 KB recorded, about 2700 a day at 4 KB/s all day: 12 B each, the
 * 16 KB settings partition's four 4 KB sectors each erased 2-3 times a day, the bonds moved at each: 10 000 cycles last
 * 8-13 years) */
#define TOP_KEY "lr/nand_top"
#define ERASE_KEY "lr/nand_erase"

static int n_save_top(uint32_t top)
{
    return settings_save_one(TOP_KEY, &top, sizeof(top));
}

static int n_save_erase(uint32_t block)
{
    return settings_save_one(ERASE_KEY, &block, sizeof(block));
}

static const struct nl_ops ops = {n_read, n_program, n_erase, n_mark_bad, n_save_top, n_save_erase};

/* Reset, identify, writes allowed (SR-1 protects every block at power-up), ECC on and buffer reads (SR-2 ECC-E, BUF:
 * W25N01GV's IT parts start with BUF 0). The block count by the chip */
static int chip_init(uint32_t *blocks)
{
    static const uint8_t id_cmd[2] = {0x9F, 0};
    uint8_t id[3], sr;
    dpd = false;
    op(0xAB);  // out of a deep power-down a warm reset left it in (W25N02KV)
    k_usleep(1500);
    int err = op(0xFF) ?: ready(&sr, 5) ?: xfer(id_cmd, 2, NULL, 0, NULL, 0, id, 3);
    if (err) {
        return err;
    }
    if (id[0] != 0xEF || id[1] != 0xAA || (id[2] != 0x21 && id[2] != 0x22)) {
        LOG_ERR("no W25N: %02x %02x %02x", id[0], id[1], id[2]);
        return -ENODEV;
    }
    chip = id[2];
    *blocks = chip == 0x22 ? 2048 : 1024;
    return set_reg(0xA0, 0) ?: get_reg(0xB0, &sr) ?: set_reg(0xB0, sr | 0x18);
}

/* The SD card's error counts (SD_ERRORS, lr_store.c) take the chip's: CRC its reads ECC could not correct, BUSY those
 * it corrected, TIMEOUT busy waits that ran out, OTHER program and erase failures. Files overwritten before they were
 * uploaded: a CARD_FULL x 2 (a their count); an erase NVS did not take the block of: NVS_FAIL, the first of a boot */
static void count_errors(void)
{
    static uint32_t seen[NL_ERR_N];
    static const uint8_t to[NL_ERR_N] = {LR_SD_BUSY, LR_SD_CRC, LR_SD_OTHER, LR_SD_TIMEOUT, LR_SD_ERR_N, LR_SD_ERR_N};
    for (int i = 0; i < NL_ERR_N; i++) {
        uint32_t d = nl_err[i] - seen[i];
        seen[i] = nl_err[i];
        if (d && to[i] < LR_SD_ERR_N) {
            lr_sd_err[to[i]] += d;
        } else if (d && i == NL_LOST) {
            lr_log(LR_CARD_FULL, 2, d, 0);
        } else if (d && seen[i] == d) {  // NVS worn out: one each erase (every 32 s) would fill the log
            lr_log(LR_NVS_FAIL, 0, d, 0);
        }
    }
}

/* The uploaded pointer (nand_log.h) in NVS: written only when a DELETE moves it on */
#define UP_KEY "lr/nand_up"

static int up_loaded(const char *key, size_t len, settings_read_cb read, void *arg, void *param)
{
    if (len == sizeof(uint32_t)) {
        read(arg, param, len);
    }
    return 0;
}

//
// The file system: /SD:/LR/BBBBSSSS.BIN is a file of the log, /SD:/LR/BOOT reads the newest boot listed, anything else
// opens and takes writes and keeps nothing (.metadata_never_index, .fseventsd: a computer's, the SD card's USB disk)
//
enum { OTHER, LOGFILE, BOOT };
struct nfile {
    bool used, wr;
    uint8_t kind;
    uint32_t file, pos;
};
static struct nfile nfiles[4];
static int dir_at;

static int kind_of(const struct fs_mount_t *mp, const char *path, uint32_t *file)
{
    const char *p = path + mp->mountp_len;
    if (!strncmp(p, "/LR/", 4) && strlen(p + 4) == 12 && !strcmp(p + 12, ".BIN")) {
        *file = strtoul(p + 4, NULL, 16);
        return LOGFILE;
    }
    return strcmp(p, "/LR/BOOT") ? OTHER : BOOT;
}

static uint32_t size_of(uint32_t file)
{
    for (int i = 0; i < nl_files(); i++) {
        uint32_t f, s;
        nl_file(i, &f, &s);
        if (f == file) {
            return s;
        }
    }
    return UINT32_MAX;
}

static int nfs_open(struct fs_file_t *fp, const char *path, fs_mode_t flags)
{
    struct nfile *f = NULL;
    for (int i = 0; i < ARRAY_SIZE(nfiles) && !f; i++) {
        f = nfiles[i].used ? NULL : &nfiles[i];
    }
    if (!f) {
        return -ENFILE;
    }
    *f = (struct nfile){.used = true, .wr = flags & FS_O_WRITE};
    f->kind = kind_of(fp->mp, path, &f->file);
    uint32_t size = f->kind == LOGFILE ? size_of(f->file) : 0;
    if (size == UINT32_MAX && !(flags & FS_O_CREATE)) {
        f->used = false;
        return -ENOENT;
    }
    f->pos = flags & FS_O_APPEND && size != UINT32_MAX ? size : 0;
    fp->filep = f;
    return 0;
}

static ssize_t nfs_read(struct fs_file_t *fp, void *dest, size_t n)
{
    struct nfile *f = fp->filep;
    int got = 0;
    if (f->kind == LOGFILE) {
        got = nl_read(f->file, f->pos, dest, n);
    } else if (f->kind == BOOT && f->pos < 2 && n >= 2) {  // the newest boot listed (lr_store.c next_boot)
        uint32_t newest = 0;
        for (int i = 0; i < nl_files(); i++) {
            uint32_t file, s;
            nl_file(i, &file, &s);
            newest = MAX(newest, file >> 16);
        }
        sys_put_le16(MIN(newest, 0xFFFF), dest);
        got = 2;
    }
    f->pos += MAX(got, 0);
    count_errors();
    doze();
    return got;
}

static ssize_t nfs_write(struct fs_file_t *fp, const void *src, size_t n)
{
    struct nfile *f = fp->filep;
    int put = f->kind == LOGFILE ? nl_append(f->file, src, n) : n;
    f->pos += MAX(put, 0);
    count_errors();
    return put;
}

static int nfs_lseek(struct fs_file_t *fp, off_t off, int whence)
{
    struct nfile *f = fp->filep;
    off_t base = whence == FS_SEEK_CUR ? f->pos : whence == FS_SEEK_END && f->kind == LOGFILE ? size_of(f->file) : 0;
    f->pos = base + off;
    return 0;
}

static off_t nfs_tell(struct fs_file_t *fp)
{
    return ((struct nfile *) fp->filep)->pos;
}

/* No program: the open page goes out once full (each second or so) or at the close; a sync each batch would leave
 * a third of each page empty (docs/nand-store.md 2) */
static int nfs_sync(struct fs_file_t *fp)
{
    doze();
    return 0;
}

static int nfs_close(struct fs_file_t *fp)
{
    struct nfile *f = fp->filep;
    int err = f->kind == LOGFILE && f->wr ? nl_flush() : 0;
    f->used = false;
    count_errors();
    doze();
    return err;
}

static int nfs_opendir(struct fs_dir_t *dp, const char *path)
{
    if (strcmp(path + dp->mp->mountp_len, "/LR")) {
        return -ENOENT;
    }
    dir_at = 0;
    dp->dirp = &dir_at;
    return 0;
}

static int nfs_readdir(struct fs_dir_t *dp, struct fs_dirent *e)
{
    e->name[0] = 0;
    if (dir_at < nl_files()) {
        uint32_t file, size;
        nl_file(dir_at++, &file, &size);
        e->type = FS_DIR_ENTRY_FILE;
        e->size = size;
        snprintf(e->name, sizeof(e->name), "%08X.BIN", file);
    }
    return 0;
}

static int nfs_closedir(struct fs_dir_t *dp)
{
    return 0;
}

static int nfs_mount(struct fs_mount_t *mp)
{
    uint32_t blocks, up = 0, top = 0, erasing = UINT32_MAX;
    int64_t t0 = k_uptime_get();
    int err = chip_init(&blocks);
    if (!err) {
        settings_subsys_init();
        settings_load_subtree_direct(UP_KEY, up_loaded, &up);
        settings_load_subtree_direct(TOP_KEY, up_loaded, &top);
        settings_load_subtree_direct(ERASE_KEY, up_loaded, &erasing);
        err = nl_mount(&ops, blocks, up, top, erasing);
    }
    lr_sd_sectors = err ? 0 : nl_total() / 512;
    mounted = !err;
    LOG_INF("NAND %02x: %d, %u files, %u ms", chip, err, nl_files(), (uint32_t) (k_uptime_get() - t0));
    lr_log(LR_CARD_ID, 0xEF, 0xEFAA00 | chip, k_uptime_get() - t0);  // the chip, and how long recovery took (ms)
    lr_log(LR_CARD_SIZE, 2, lr_sd_sectors, nl_files());  // x 2: a NAND (lr_sync.py)
    count_errors();
    doze();
    return err;
}

static int nfs_unmount(struct fs_mount_t *mp)
{
    int err = mounted ? nl_flush() : 0;
    mounted = false;
    doze();
    return err;
}

static int nfs_unlink(struct fs_mount_t *mp, const char *path)
{
    uint32_t file;
    bool moved = false;
    if (kind_of(mp, path, &file) != LOGFILE) {
        return 0;
    }
    int err = nl_delete(file, &moved);
    if (moved) {
        uint32_t up = nl_up();
        settings_save_one(UP_KEY, &up, sizeof(up));
    }
    return err;
}

static int nfs_mkdir(struct fs_mount_t *mp, const char *path)
{
    return 0;
}

static int nfs_statvfs(struct fs_mount_t *mp, const char *path, struct fs_statvfs *st)
{
    st->f_bsize = st->f_frsize = NL_PAGE;
    st->f_blocks = nl_total() / NL_PAGE;
    st->f_bfree = (nl_total() - MIN(nl_used(), nl_total())) / NL_PAGE;
    return 0;
}

static const struct fs_file_system_t nfs = {
    .open = nfs_open,
    .read = nfs_read,
    .write = nfs_write,
    .lseek = nfs_lseek,
    .tell = nfs_tell,
    .sync = nfs_sync,
    .close = nfs_close,
    .opendir = nfs_opendir,
    .readdir = nfs_readdir,
    .closedir = nfs_closedir,
    .mount = nfs_mount,
    .unmount = nfs_unmount,
    .unlink = nfs_unlink,
    .mkdir = nfs_mkdir,
    .statvfs = nfs_statvfs,
};

static struct fs_mount_t mount_point = {.type = FS_TYPE_EXTERNAL_BASE, .mnt_point = "/SD:"};

//
// sdcard.h
//
/* The chip's four lines (SCK MOSI MISO, then CS) */
#define NAND_LINE(node, prop, idx) NRF_GET_PIN(DT_PROP_BY_IDX(node, prop, idx)),
static const uint32_t lines[] = {
    DT_FOREACH_PROP_ELEM(DT_CHILD(DT_NODELABEL(nand_spi_default), group1), psels, NAND_LINE)
    NRF_GPIO_PIN_MAP(DT_PROP(DT_GPIO_CTLR_BY_IDX(NAND_SPI, cs_gpios, 0), port), DT_GPIO_PIN_BY_IDX(NAND_SPI, cs_gpios, 0)),
};

/* System OFF (lr_power.c, its supply going off): the lines let go, so they do not feed the chip */
void sd_lines_release(void)
{
    for (int i = 0; i < ARRAY_SIZE(lines); i++) {
        nrf_gpio_cfg_default(lines[i]);
    }
}

/* The chip's supply, the LDO's EN (sd-pwr-gpios): on at the first mount and kept on (the chip idles at 1-50 uA);
 * off and on again only for a chip that stopped answering (mount_sd_card_reset), as sdcard.c does for a card: off
 * 300 ms, its lines let go meanwhile, then back as the SPI driver set them. None on the board: nothing to do */
#if DT_NODE_HAS_PROP(DT_PATH(zephyr_user), sd_pwr_gpios)
static const struct gpio_dt_spec pwr = GPIO_DT_SPEC_GET(DT_PATH(zephyr_user), sd_pwr_gpios);
static uint32_t lines_cnf[ARRAY_SIZE(lines)], lines_out[ARRAY_SIZE(lines)];
static bool pwr_on, pwr_off;
static int64_t pwr_off_at;
#endif

void sd_power_off(void)
{
    unmount_sd_card();
#if DT_NODE_HAS_PROP(DT_PATH(zephyr_user), sd_pwr_gpios)
    if (pwr_off) {
        return;
    }
    for (int i = 0; i < ARRAY_SIZE(lines); i++) {
        uint32_t pin = lines[i];
        lines_cnf[i] = nrf_gpio_pin_port_decode(&pin)->PIN_CNF[pin];
        lines_out[i] = nrf_gpio_pin_out_read(lines[i]);
    }
    sd_lines_release();
    gpio_pin_configure_dt(&pwr, GPIO_OUTPUT_INACTIVE);
    pwr_off = true;
    pwr_off_at = k_uptime_get();
#endif
}

/* tVSL: 50 us from VCC up to the first command; the chip then loads page 0 (busy, which ready() waits out) */
static void power_on(void)
{
#if DT_NODE_HAS_PROP(DT_PATH(zephyr_user), sd_pwr_gpios)
    if (!pwr_on) {  // the first time off then on, as sdcard.c does at boot: CS was driven high from the SPI driver's
        pwr_on = true;  // init with the LDO off, feeding the chip through its input diodes
        sd_power_off();
    }
    if (!pwr_off) {
        return;
    }
    int64_t left = 300 - (k_uptime_get() - pwr_off_at);
    if (left > 0) {
        k_msleep(left);
    }
    gpio_pin_set_dt(&pwr, 1);
    k_msleep(2);
    for (int i = 0; i < ARRAY_SIZE(lines); i++) {
        uint32_t pin = lines[i];
        nrf_gpio_pin_write(lines[i], lines_out[i]);  // the level first: CS comes back high (deselected)
        nrf_gpio_pin_port_decode(&pin)->PIN_CNF[pin] = lines_cnf[i];
    }
    pwr_off = false;
#endif
}

static int mount_once(void)
{
    static bool registered;
    if (!registered) {
        int err = fs_register(FS_TYPE_EXTERNAL_BASE, &nfs);
        if (err) {
            return err;
        }
        registered = true;
    }
    if (mounted) {
        return 0;
    }
    int err = fs_mount(&mount_point);
    lr_log(LR_MOUNT, 0, err, 0);
    return err;
}

int mount_sd_card(void)
{
    power_on();
    return mount_once();
}

int mount_sd_card_once(void)
{
    power_on();
    return mount_once();
}

int mount_sd_card_reset(void)
{
    sd_power_off();
    power_on();
    return mount_once();
}

void unmount_sd_card(void)
{
    if (mounted) {
        fs_unmount(&mount_point);
    }
    mounted = false;
}

int format_sd_card(uint32_t kb, void *work, uint32_t len)
{
    return -EEXIST;  // FORMAT is for a card with no file system
}

void sd_idle_clocks(void)
{
}

void sd_off()
{
    unmount_sd_card();
}

void sd_on()
{
    mount_sd_card();
}

bool is_sd_on()
{
    return mounted;
}
