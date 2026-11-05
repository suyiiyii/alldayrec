// Host check of src/usb.c's disk: only a read or write of the lent card that worked counts as the computer using it
// (usb_disk_used, which lr_store.c's 3 minutes left alone go by). X1 10-17: macOS retried a failing read now and then
// and each try put the 3 minutes back, so the card stayed lent for good.
//   d=$(mktemp -d) && mkdir -p $d/zephyr/{drivers,logging,storage,usb/class,sys} $d/hal && for h in hal/nrf_power \
//   zephyr/drivers/disk zephyr/logging/log zephyr/storage/disk_access zephyr/usb/class/usbd_msc zephyr/drivers/uart \
//   zephyr/sys/iterable_sections zephyr/usb/usbd; do touch $d/$h.h; done && cc -w -I$d -o $d/usb_check \
//   test/usb_check.c && $d/usb_check
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>

// --- the Zephyr API usb.c uses
#define CONFIG_LR_NAME_PREFIX "LR"
#define LOG_MODULE_REGISTER(...)
#define LOG_INF(...)
#define LOG_ERR(...)
#define K_FOREVER 0
#define K_SECONDS(x) (x)
#define K_PRIO_PREEMPT(x) (x)
#define K_WORK_DEFINE(name, fn) struct k_work name
#define K_WORK_DELAYABLE_DEFINE(name, fn) struct k_work_delayable name
#define USBD_DEVICE_DEFINE(name, ...) struct usbd_context name
#define USBD_DESC_LANG_DEFINE(name) int name
#define USBD_DESC_MANUFACTURER_DEFINE(name, s) int name
#define USBD_DESC_PRODUCT_DEFINE(name, s) int name
#define USBD_DESC_SERIAL_NUMBER_DEFINE(name) int name
#define USBD_CONFIGURATION_DEFINE(name, ...) int name
#define USBD_DEFINE_MSC_LUN(...)
#define STRUCT_SECTION_FOREACH_ALTERNATE(sec, type, var) for (struct { struct { const char *name; } *c_data; } *var = 0; var;)
enum { DISK_STATUS_OK, DISK_STATUS_NOMEDIA = 2 };
enum { USBD_MSG_VBUS_READY, USBD_MSG_VBUS_REMOVED, USBD_MSG_CDC_ACM_LINE_CODING };
enum { USBD_SPEED_FS, USB_BCC_MISCELLANEOUS = 0xEF, UART_LINE_CTRL_BAUD_RATE = 1 };
struct device;
struct k_mutex { int x; };
struct k_work { int x; };
struct k_work_delayable { struct k_work work; };
typedef void *k_tid_t;
struct usbd_context { int x; };
struct usbd_msg { int type; const struct device *dev; };
struct disk_info;
struct disk_operations {
    int (*init)(struct disk_info *);
    int (*status)(struct disk_info *);
    int (*read)(struct disk_info *, uint8_t *, uint32_t, uint32_t);
    int (*write)(struct disk_info *, const uint8_t *, uint32_t, uint32_t);
    int (*ioctl)(struct disk_info *, uint8_t, void *);
};
struct disk_info { const char *name; const struct disk_operations *ops; };
static uint32_t now_ms;
static uint32_t k_uptime_get_32(void) { return now_ms; }
static int k_mutex_lock(struct k_mutex *m, int t) { return 0; }
static int k_mutex_unlock(struct k_mutex *m) { return 0; }
static int k_work_submit(struct k_work *w) { return 0; }
static int k_work_schedule(struct k_work_delayable *w, int t) { return 0; }
static void k_msleep(int ms) {}
static void k_thread_priority_set(k_tid_t t, int p) {}
static int sd_err;  // what the card's next read or write returns
static int disk_access_status(const char *d) { return DISK_STATUS_OK; }
static int disk_access_read(const char *d, uint8_t *b, uint32_t s, uint32_t n) { return sd_err; }
static int disk_access_write(const char *d, const uint8_t *b, uint32_t s, uint32_t n) { return sd_err; }
static int disk_access_ioctl(const char *d, uint8_t c, void *b) { return sd_err; }
static int disk_access_register(struct disk_info *d) { return 0; }
static int usbd_enable(struct usbd_context *c) { return 0; }
static int usbd_disable(struct usbd_context *c) { return 0; }
static int usbd_init(struct usbd_context *c) { return 0; }
static bool usbd_can_detect_vbus(struct usbd_context *c) { return true; }
static int usbd_add_descriptor(struct usbd_context *c, void *d) { return 0; }
static int usbd_add_configuration(struct usbd_context *c, int s, void *d) { return 0; }
static int usbd_register_class(struct usbd_context *c, const char *n, int s, int x) { return 0; }
static void usbd_device_set_code_triple(struct usbd_context *c, int s, int a, int b, int d) {}
static int usbd_msg_register_cb(struct usbd_context *c, void (*cb)(struct usbd_context *const, const struct usbd_msg *)) { return 0; }
static int uart_line_ctrl_get(const struct device *d, int c, uint32_t *v) { return 0; }
static int nrf_power, *NRF_POWER = &nrf_power;
static bool nrf_power_usbregstatus_vbusdet_get(int *p) { return true; }
void lr_store_shutdown(void) {}
void lr_store_lend(bool on) {}
void lr_log_reset(uint8_t g) {}
struct k_mutex write_sdcard_mutex;
const k_tid_t usbd_msc;

#include "../src/usb.c"

int main(void)
{
    uint8_t buf[512];
    usb_disk_lent = true, usb_disk_used = 1000, now_ms = 5000;
    sd_err = -EIO;  // the card fails: the computer tries again and again, and the card is still left alone
    assert(lr_disk_read(&lr_disk, buf, 0, 1) == -EIO && usb_disk_used == 1000);
    now_ms += 30000;
    assert(lr_disk_write(&lr_disk, buf, 0, 1) == -EIO && usb_disk_used == 1000);
    sd_err = 0;  // it reads and writes: in use
    assert(lr_disk_read(&lr_disk, buf, 0, 1) == 0 && usb_disk_used == now_ms);
    now_ms += 10;
    assert(lr_disk_write(&lr_disk, buf, 0, 1) == 0 && usb_disk_used == now_ms);
    usb_disk_lent = false, now_ms += 10;  // taken back: no medium, nothing used
    assert(lr_disk_read(&lr_disk, buf, 0, 1) == -EIO && lr_disk_write(&lr_disk, buf, 0, 1) == -EIO);
    assert(usb_disk_used == now_ms - 10);
    printf("usb_check ok\n");
    return 0;
}
