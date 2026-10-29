#include "transport.h"

#include <string.h>
#include <hal/nrf_power.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/bluetooth/hci.h>
#include <zephyr/bluetooth/l2cap.h>
#include <zephyr/bluetooth/services/bas.h>
#include <zephyr/bluetooth/uuid.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/sensor.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/settings/settings.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/sys/byteorder.h>
#include <zephyr/sys/ring_buffer.h>

#include "config.h"
#include "utils.h"
// #include "nfc.h"
#include "button.h"
#include "lib/battery/battery.h"
#include "mic.h"
#include "sdcard.h"
#include "speaker.h"
#include "lr_log.h"
#include "lr_power.h"
#include "lr_store.h"
// #include "friend.h"
LOG_MODULE_REGISTER(transport, CONFIG_LOG_DEFAULT_LEVEL);

extern bool is_connected;
struct bt_conn *current_connection = NULL;
uint16_t current_mtu = 0;
uint16_t current_package_index = 0;

//
// Internal
//

struct k_mutex write_sdcard_mutex;
K_SEM_DEFINE(pusher_wake_sem, 0, 1);
static atomic_t connection_generation;

static struct bt_conn_cb _callback_references;
static void dfu_ccc_config_changed_handler(const struct bt_gatt_attr *attr, uint16_t value);
static ssize_t dfu_control_point_write_handler(struct bt_conn *conn,
                                               const struct bt_gatt_attr *attr,
                                               const void *buf,
                                               uint16_t len,
                                               uint16_t offset,
                                               uint8_t flags);

//
// Service and Characteristic
//
// The record stream's service (lr_store.c), advertised: clients find the recorder by it
static struct bt_uuid_128 lr_service_uuid =
    BT_UUID_INIT_128(BT_UUID_128_ENCODE(0x7c3e0000, 0x1a2b, 0x4c5d, 0x8e9f, 0x6c6976657265));

// Nordic Legacy DFU service with UUID 00001530-1212-EFDE-1523-785FEABCD123
// exposes following characteristics:
// - Control point (UUID 00001531-1212-EFDE-1523-785FEABCD123) to start the OTA update process (write/notify)
static struct bt_uuid_128 dfu_service_uuid =
    BT_UUID_INIT_128(BT_UUID_128_ENCODE(0x00001530, 0x1212, 0xEFDE, 0x1523, 0x785FEABCD123));
static struct bt_uuid_128 dfu_control_point_uuid =
    BT_UUID_INIT_128(BT_UUID_128_ENCODE(0x00001531, 0x1212, 0xEFDE, 0x1523, 0x785FEABCD123));
// packet and revision as the Adafruit app-side DFU service has them: Nordic's Android DFU library looks for both, and
// revision 1 tells it this is the application, to be sent into the bootloader first
static struct bt_uuid_128 dfu_packet_uuid =
    BT_UUID_INIT_128(BT_UUID_128_ENCODE(0x00001532, 0x1212, 0xEFDE, 0x1523, 0x785FEABCD123));
static struct bt_uuid_128 dfu_revision_uuid =
    BT_UUID_INIT_128(BT_UUID_128_ENCODE(0x00001534, 0x1212, 0xEFDE, 0x1523, 0x785FEABCD123));
static ssize_t dfu_ignore(struct bt_conn *conn, const struct bt_gatt_attr *attr, const void *buf, uint16_t len,
                          uint16_t offset, uint8_t flags)
{
    return len;
}
static ssize_t dfu_revision(struct bt_conn *conn, const struct bt_gatt_attr *attr, void *buf, uint16_t len, uint16_t offset)
{
    uint8_t v[2] = {1, 0};
    return bt_gatt_attr_read(conn, attr, buf, len, offset, v, sizeof(v));
}

static struct bt_gatt_attr dfu_service_attr[] = {
    BT_GATT_PRIMARY_SERVICE(&dfu_service_uuid),
    BT_GATT_CHARACTERISTIC(&dfu_control_point_uuid.uuid,
                           BT_GATT_CHRC_WRITE | BT_GATT_CHRC_NOTIFY,
                           BT_GATT_PERM_WRITE_ENCRYPT,  // only a bonded phone sends the recorder into its bootloader
                           NULL,
                           dfu_control_point_write_handler,
                           NULL),
    BT_GATT_CCC(dfu_ccc_config_changed_handler, BT_GATT_PERM_READ | BT_GATT_PERM_WRITE_ENCRYPT),
    BT_GATT_CHARACTERISTIC(&dfu_packet_uuid.uuid, BT_GATT_CHRC_WRITE_WITHOUT_RESP, BT_GATT_PERM_WRITE_ENCRYPT, NULL,
                           dfu_ignore, NULL),
    BT_GATT_CHARACTERISTIC(&dfu_revision_uuid.uuid, BT_GATT_CHRC_READ, BT_GATT_PERM_READ, dfu_revision, NULL, NULL),
};

static struct bt_gatt_service dfu_service = BT_GATT_SERVICE(dfu_service_attr);
// Acceleration data
// this code activates the onboard accelerometer. some cute ideas may include shaking the necklace to color strobe
//
static struct sensors mega_sensor;
static struct device *lsm6dsl_dev;
// Arbritrary uuid, feel free to change
static struct bt_uuid_128 accel_uuid =
    BT_UUID_INIT_128(BT_UUID_128_ENCODE(0x32403790, 0x0000, 0x1000, 0x7450, 0xBF445E5829A2));
static struct bt_uuid_128 accel_uuid_x =
    BT_UUID_INIT_128(BT_UUID_128_ENCODE(0x32403791, 0x0000, 0x1000, 0x7450, 0xBF445E5829A2));

static void accel_ccc_config_changed_handler(const struct bt_gatt_attr *attr, uint16_t value);
static ssize_t accel_data_read_characteristic(struct bt_conn *conn,
                                              const struct bt_gatt_attr *attr,
                                              void *buf,
                                              uint16_t len,
                                              uint16_t offset);

static struct bt_gatt_attr accel_service_attr[] = {
    BT_GATT_PRIMARY_SERVICE(&accel_uuid), // primary description
    BT_GATT_CHARACTERISTIC(&accel_uuid_x.uuid,
                           BT_GATT_CHRC_READ | BT_GATT_CHRC_NOTIFY,
                           BT_GATT_PERM_READ,
                           accel_data_read_characteristic,
                           NULL,
                           NULL),                                                          // data type
    BT_GATT_CCC(accel_ccc_config_changed_handler, BT_GATT_PERM_READ | BT_GATT_PERM_WRITE), // scheduler
};
static struct bt_gatt_service accel_service = BT_GATT_SERVICE(accel_service_attr);

static ssize_t accel_data_read_characteristic(struct bt_conn *conn,
                                              const struct bt_gatt_attr *attr,
                                              void *buf,
                                              uint16_t len,
                                              uint16_t offset)
{
    LOG_INF("Acceleration data read characteristic");
    int axis_mode = 6; // 3 for accel, 6 for (also) gyro
    return bt_gatt_attr_read(conn, attr, buf, len, offset, &axis_mode, sizeof(axis_mode));
}

#define ACCEL_REFRESH_INTERVAL 1000 // 1.0 seconds

void broadcast_accel(struct k_work *work_item);
K_WORK_DELAYABLE_DEFINE(accel_work, broadcast_accel);

void broadcast_accel(struct k_work *work_item)
{

    sensor_sample_fetch_chan(lsm6dsl_dev, SENSOR_CHAN_ACCEL_XYZ);
    sensor_channel_get(lsm6dsl_dev, SENSOR_CHAN_ACCEL_X, &mega_sensor.a_x);
    sensor_channel_get(lsm6dsl_dev, SENSOR_CHAN_ACCEL_Y, &mega_sensor.a_y);
    sensor_channel_get(lsm6dsl_dev, SENSOR_CHAN_ACCEL_Z, &mega_sensor.a_z);

    sensor_sample_fetch_chan(lsm6dsl_dev, SENSOR_CHAN_GYRO_XYZ);
    sensor_channel_get(lsm6dsl_dev, SENSOR_CHAN_GYRO_X, &mega_sensor.g_x);
    sensor_channel_get(lsm6dsl_dev, SENSOR_CHAN_GYRO_Y, &mega_sensor.g_y);
    sensor_channel_get(lsm6dsl_dev, SENSOR_CHAN_GYRO_Z, &mega_sensor.g_z);

    // only time mega sensor is changed is through here (hopefully),  so no chance of race condition
    int err = bt_gatt_notify(current_connection, &accel_service.attrs[1], &mega_sensor, sizeof(mega_sensor));
    if (err) {
        LOG_ERR("Error updating Accelerometer data");
    }
    k_work_reschedule(&accel_work, K_MSEC(ACCEL_REFRESH_INTERVAL));
}

struct gpio_dt_spec accel_gpio_pin = {.port = DEVICE_DT_GET(DT_NODELABEL(gpio1)),
                                      .pin = 8,
                                      .dt_flags = GPIO_INT_DISABLE};

// use d4,d5
static void accel_ccc_config_changed_handler(const struct bt_gatt_attr *attr, uint16_t value)
{
    if (value == BT_GATT_CCC_NOTIFY) {
        LOG_INF("Client subscribed for notifications");
    } else if (value == 0) {
        LOG_INF("Client unsubscribed from notifications");
    } else {
        LOG_ERR("Invalid CCC value: %u", value);
    }
}

int accel_start()
{
    struct sensor_value odr_attr;
    lsm6dsl_dev = DEVICE_DT_GET_ONE(st_lsm6dsl);
    k_msleep(50);
    if (lsm6dsl_dev == NULL) {
        LOG_ERR("Could not get LSM6DSL device");
        return 0;
    }
    if (!device_is_ready(lsm6dsl_dev)) {
        LOG_ERR("LSM6DSL: not ready");
        return 0;
    }
    odr_attr.val1 = 10;
    odr_attr.val2 = 0;

    if (gpio_is_ready_dt(&accel_gpio_pin)) {
        LOG_PRINTK("Speaker Pin ready\n");
    } else {
        LOG_PRINTK("Error setting up speaker Pin\n");
        return -1;
    }
    if (gpio_pin_configure_dt(&accel_gpio_pin, GPIO_OUTPUT_INACTIVE) < 0) {
        LOG_PRINTK("Error setting up Haptic Pin\n");
        return -1;
    }
    gpio_pin_set_dt(&accel_gpio_pin, 1);
    if (sensor_attr_set(lsm6dsl_dev, SENSOR_CHAN_ACCEL_XYZ, SENSOR_ATTR_SAMPLING_FREQUENCY, &odr_attr) < 0) {
        LOG_ERR("Cannot set sampling frequency for Accelerometer.");
        return 0;
    }
    if (sensor_attr_set(lsm6dsl_dev, SENSOR_CHAN_GYRO_XYZ, SENSOR_ATTR_SAMPLING_FREQUENCY, &odr_attr) < 0) {
        LOG_ERR("Cannot set sampling frequency for gyro.");
        return 0;
    }
    if (sensor_sample_fetch(lsm6dsl_dev) < 0) {
        LOG_ERR("Sensor sample update error");
        return 0;
    }

    LOG_INF("Accelerometer is ready for use \n");

    return 1;
}
// Advertisement data: 31 bytes at most, so the board name (LR-X1) here and the full name (LR-X1-3F2A) in the scan response
static const struct bt_data bt_ad[] = {
    BT_DATA_BYTES(BT_DATA_FLAGS, (BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR)),
    BT_DATA(BT_DATA_UUID128_ALL, lr_service_uuid.val, sizeof(lr_service_uuid.val)),
    BT_DATA(BT_DATA_NAME_SHORTENED, CONFIG_LR_NAME_PREFIX, sizeof(CONFIG_LR_NAME_PREFIX) - 1),
};

static char lr_name[CONFIG_BT_DEVICE_NAME_MAX + 1];

// The recorder's state for a phone that is only scanning (the app's device list): company 0xFFFF (for tests), then
// bit 0 bound to a phone, bit 1 a phone connected, bit 2 recording paused, bits 3-4 lr_charge() (0 on battery,
// 1 charging, 2 full); the battery, mV / 20 (BAS's ~1 minute average; 0: not read yet); the card, the store's error
// negated (lr_store_err: 0 recording to it, 19 none, 28 full, 16 lent to USB, others failed). A phone that cannot
// connect still sees it alive, and how; one that reads only the first byte (apps before it) is not affected
#define LR_BOUND BIT(0)
#define LR_CONNECTED BIT(1)
#define LR_PAUSED BIT(2)
static uint8_t lr_status[5] = {0xFF, 0xFF};
static uint8_t lr_mv20;  // the battery work's

// Scan response data. The DIS and DFU UUIDs are left out for room: no client looks for them in a scan (the DFU library
// finds the service once connected), and the services themselves are still there
static struct bt_data bt_sd[] = {
    {.type = BT_DATA_NAME_COMPLETE, .data_len = 0, .data = (const uint8_t *) lr_name},
    BT_DATA(BT_DATA_MANUFACTURER_DATA, lr_status, sizeof(lr_status)),
};

/* The unit's name: the board name and the last two bytes of its identity address, which stays with the chip */
static void lr_set_name(void)
{
    bt_addr_le_t addr;
    size_t n = 1;
    bt_id_get(&addr, &n);
    snprintk(lr_name, sizeof(lr_name), "%s-%02X%02X", CONFIG_LR_NAME_PREFIX, addr.a.val[1], addr.a.val[0]);
    bt_set_name(lr_name);
    bt_sd[0].data_len = strlen(lr_name);
    LOG_INF("BLE name %s", lr_name);
}

//
// Bound, paused, advertising. The recorder keeps nothing until a phone binds it, nor while a phone has paused it;
// the frames are still counted, the frame number being the offline store's clock (lr_store.h).
// ponytail: mic and codec run while nothing is kept; turn the mic off (and sync the clock again after) to save power.
//

#define UNENCRYPTED_S 30
static void unencrypted_check(struct k_work *work);
static K_WORK_DELAYABLE_DEFINE(unencrypted_work, unencrypted_check);

volatile uint8_t lr_led_paired;  // main.c: half-seconds of the green "paired" blinks left
static atomic_t bonds;           // bonded phones
static volatile bool paused;
static int64_t pause_end_ms;     // uptime the pause ends at

static void count_bond(const struct bt_bond_info *info, void *n)
{
    (*(int *) n)++;
}

int lr_bond_count(void)
{
    return atomic_get(&bonds);
}

bool lr_paused(void)
{
    return paused;
}

static bool capturing(void)
{
    return atomic_get(&bonds) && !lr_paused();
}

/* Advertising never stops: connectable while no phone is connected; while one is (BT_MAX_CONN=1 leaves no room for
 * another), scannable only and slowly, so a phone looking for recorders still sees this one, bound and busy. The scan
 * response carries the state (lr_status). */
/* The scan response's state, battery and card as they are now: true if any changed */
static bool status_now(void)
{
    uint8_t was[3];
    memcpy(was, lr_status + 2, 3);
    lr_status[2] = (atomic_get(&bonds) ? LR_BOUND : 0) | (current_connection ? LR_CONNECTED : 0) |
                   (lr_paused() ? LR_PAUSED : 0) | lr_charge() << 3;
    lr_status[3] = lr_mv20;
    lr_status[4] = MIN(-lr_store_err(), 255);
    return memcmp(was, lr_status + 2, 3);
}

static void adv_restart(struct k_work *work)
{
    status_now();
    bt_le_adv_stop();
    int err = current_connection
                  ? bt_le_adv_start(BT_LE_ADV_PARAM(BT_LE_ADV_OPT_SCANNABLE | BT_LE_ADV_OPT_USE_IDENTITY,
                                                    BT_GAP_ADV_SLOW_INT_MIN, BT_GAP_ADV_SLOW_INT_MAX, NULL),
                                    bt_ad, ARRAY_SIZE(bt_ad), bt_sd, ARRAY_SIZE(bt_sd))
                  : bt_le_adv_start(BT_LE_ADV_CONN_ONE_TIME, bt_ad, ARRAY_SIZE(bt_ad), bt_sd, ARRAY_SIZE(bt_sd));
    // A connection just gone whose object is not free yet: try again shortly. Not from the recycled() callback: stopping
    // a connectable advertiser frees its own connection object, which calls recycled() too, and the restart looped
    // (10-04: the XIAO went unseen when a pause ended while no phone was connected)
    if (err) {
        LOG_WRN("advertising: %d", err);
        k_work_reschedule(k_work_delayable_from_work(work), K_MSEC(500));
    }
}
static K_WORK_DELAYABLE_DEFINE(adv_work, adv_restart);

static void adv_refresh(void)
{
    k_work_reschedule(&adv_work, K_NO_WAIT);
}

static void count_bonds(void)
{
    int n = 0;
    bt_foreach_bond(BT_ID_DEFAULT, count_bond, &n);
    atomic_set(&bonds, n);
    LOG_INF("%d bonded phone(s)", n);
    adv_refresh();
}

static void pause_end(struct k_work *work)
{
    paused = false;
    LOG_INF("recording again");
    adv_refresh();
}
static K_WORK_DELAYABLE_DEFINE(pause_work, pause_end);

void lr_pause(uint32_t s)
{
    if (!s) {
        k_work_cancel_delayable(&pause_work);
        pause_end(NULL);
        return;
    }
    s = MIN(s, 24 * 3600);
    pause_end_ms = k_uptime_get() + s * 1000LL;
    paused = true;
    k_work_reschedule(&pause_work, K_SECONDS(s));
    LOG_INF("paused for %u s", s);
    adv_refresh();
}

uint32_t lr_pause_left(void)
{
    return paused ? DIV_ROUND_UP(MAX(pause_end_ms - k_uptime_get(), 0), 1000) : 0;
}

//
// State and Characteristics
//

//
// DFU Service Handlers
//

static void dfu_ccc_config_changed_handler(const struct bt_gatt_attr *attr, uint16_t value)
{
    if (value == BT_GATT_CCC_NOTIFY) {
        LOG_INF("Client subscribed for notifications");
    } else if (value == 0) {
        LOG_INF("Client unsubscribed from notifications");
    } else {
        LOG_INF("Invalid CCC value: %u", value);
    }
}

static ssize_t dfu_control_point_write_handler(struct bt_conn *conn,
                                               const struct bt_gatt_attr *attr,
                                               const void *buf,
                                               uint16_t len,
                                               uint16_t offset,
                                               uint8_t flags)
{
    LOG_INF("dfu_control_point_write_handler");
    if (len == 1 && ((uint8_t *) buf)[0] == 0x57) {  // ours: into the UF2 bootloader (USB drive), as the 1200 bps touch
        lr_store_shutdown();
        lr_log_reset(0x57);
    } else if (len == 1 && ((uint8_t *) buf)[0] == 0x06) {
        lr_store_shutdown();  // what a phone in phone mode has not acked: onto the card first
        lr_log_reset(0xA8);
    } else if (len == 2 && ((uint8_t *) buf)[0] == 0x01) {
        uint8_t notification_value = 0x10;
        bt_gatt_notify(conn, attr, &notification_value, sizeof(notification_value));
        lr_store_shutdown();
        lr_log_reset(0xA8);
    }
    return len;
}

//
// Battery Service Handlers
//

#define BATTERY_REFRESH_INTERVAL 15000 // 15 seconds

void broadcast_battery_level(struct k_work *work_item);

K_WORK_DELAYABLE_DEFINE(battery_work, broadcast_battery_level);

void broadcast_battery_level(struct k_work *work_item)
{
    static uint32_t avg4;  // 4 x the readings' running average (~1 minute): on a flat stretch 20 mV is ~10 points
    uint16_t battery_millivolt;
    uint8_t battery_percentage;
    int err = battery_get_millivolt(&battery_millivolt);
    if (!err) {
        avg4 = avg4 ? avg4 - avg4 / 4 + battery_millivolt : 4 * battery_millivolt;
        err = battery_get_percentage(&battery_percentage, avg4 / 4);
        lr_mv20 = MIN(avg4 / 4 / 20, 255);
    }
    if (status_now()) {  // the advertiser's copy too; not advertising (-EAGAIN): the next start takes it
        bt_le_adv_update_data(bt_ad, ARRAY_SIZE(bt_ad), bt_sd, ARRAY_SIZE(bt_sd));
    }
    if (!err) {

        LOG_PRINTK("Battery at %d mV (capacity %d%%)\n", battery_millivolt, battery_percentage);

        // Use the Zephyr BAS function to set (and notify) the battery level
        int err = bt_bas_set_battery_level(battery_percentage);
        if (err) {
            LOG_ERR("Error updating battery level: %d", err);
        }
    } else {
        LOG_ERR("Failed to read battery level");
    }

    k_work_reschedule(&battery_work, K_MSEC(BATTERY_REFRESH_INTERVAL));
}

//
// Connection Callbacks
//

static void _transport_connected(struct bt_conn *conn, uint8_t err)
{
    struct bt_conn_info info = {0};

    err = bt_conn_get_info(conn, &info);
    if (err) {
        LOG_ERR("Failed to get connection info (err %d)", err);
        bt_conn_unref(conn);
        return;
    }

    LOG_INF("bluetooth activated");

    if (current_connection != NULL) {
        bt_conn_unref(current_connection);
    }
    current_connection = bt_conn_ref(conn);
    current_mtu = info.le.data_len->tx_max_len;
    LOG_INF("Transport connected");
    lr_log(LR_CONNECT, 0, info.le.interval | info.le.latency << 16, info.le.timeout);
    LOG_DBG("Interval: %d, latency: %d, timeout: %d", info.le.interval, info.le.latency, info.le.timeout);
    LOG_DBG("TX PHY %s, RX PHY %s", phy2str(info.le.phy->tx_phy), phy2str(info.le.phy->rx_phy));
    LOG_DBG("LE data len updated: TX (len: %d time: %d) RX (len: %d time: %d)",
            info.le.data_len->tx_max_len,
            info.le.data_len->tx_max_time,
            info.le.data_len->rx_max_len,
            info.le.data_len->rx_max_time);

    // ask for encryption at once: a bonded phone starts it with its keys, a new one pairs. macOS does not pair on its
    // own when a read fails for want of encryption (10-03)
    bt_conn_set_security(conn, BT_SECURITY_L2);
    k_work_reschedule(&unencrypted_work, K_SECONDS(UNENCRYPTED_S));

    is_connected = true;
    atomic_inc(&connection_generation);
    k_sem_give(&pusher_wake_sem);
    adv_refresh();
}

static void _transport_disconnected(struct bt_conn *conn, uint8_t err)
{
    is_connected = false;

    LOG_INF("Transport disconnected");
    lr_log(LR_DISCONNECT, err, 0, 0);

    if (current_connection != NULL) {
        bt_conn_unref(current_connection);
        current_connection = NULL;
    }
    current_mtu = 0;
    k_sem_give(&pusher_wake_sem);
    adv_refresh();
}

static bool _le_param_req(struct bt_conn *conn, struct bt_le_conn_param *param)
{
    LOG_INF("Transport connection parameters update request received.");
    LOG_DBG("Minimum interval: %d, Maximum interval: %d", param->interval_min, param->interval_max);
    LOG_DBG("Latency: %d, Timeout: %d", param->latency, param->timeout);

    return true;
}

static void _le_param_updated(struct bt_conn *conn, uint16_t interval, uint16_t latency, uint16_t timeout)
{
    LOG_INF("Connection parameters updated.");
    lr_log(LR_PARAM, 0, interval | latency << 16, timeout);
    LOG_DBG("[ interval: %d, latency: %d, timeout: %d ]", interval, latency, timeout);
}

static void _le_phy_updated(struct bt_conn *conn, struct bt_conn_le_phy_info *param)
{
    lr_log(LR_PHY, 0, param->tx_phy, param->rx_phy);
}

static void _le_data_length_updated(struct bt_conn *conn, struct bt_conn_le_data_len_info *info)
{
    LOG_DBG("LE data len updated: TX (len: %d time: %d)"
            " RX (len: %d time: %d)",
            info->tx_max_len,
            info->tx_max_time,
            info->rx_max_len,
            info->rx_max_time);
    current_mtu = info->tx_max_len;
    lr_log(LR_MTU, 1, info->tx_max_len, info->rx_max_len);
    k_sem_give(&pusher_wake_sem);
}

static void att_mtu_updated(struct bt_conn *conn, uint16_t tx, uint16_t rx)
{
    lr_log(LR_MTU, 0, tx, rx);
}
static struct bt_gatt_cb gatt_cb = {.att_mtu_updated = att_mtu_updated};

//
// Pairing: every characteristic but battery and device info needs an encrypted link (LESC, Just Works). Any phone may
// pair at any time; which recorder a phone binds, and not taking one bound to another phone, is the app's choice
// (development: phones change often). Bonded phones reconnect with their keys, no pairing.
//

static void pairing_complete(struct bt_conn *conn, bool bonded)
{
    LOG_INF("paired, bonded %d", bonded);
    lr_log(LR_PAIRED, bonded, 0, 0);
    lr_led_paired = 4;  // main.c: green twice
    count_bonds();
}

static void pairing_failed(struct bt_conn *conn, enum bt_security_err reason)
{
    LOG_WRN("pairing failed: %d", reason);
    lr_log(LR_PAIR_FAIL, reason, 0, 0);
}

static void bond_deleted(uint8_t id, const bt_addr_le_t *peer)
{
    count_bonds();
}

struct bond_find {
    const bt_addr_le_t *addr;
    bool found;
};

static void bond_find(const struct bt_bond_info *info, void *data)
{
    struct bond_find *f = data;
    f->found |= bt_addr_le_eq(&info->addr, f->addr);
}

/* "PIN or key missing": keys that do not match. A bond of ours the phone no longer has (app reinstalled, Bluetooth
 * reset) goes, so it pairs afresh on its next connection (10-03: the Mac stuck at "PIN or key missing"). A phone this
 * recorder does not know (another phone bound it) keeps its link, unencrypted: the app's first encrypted
 * read fails and it shows it was kicked; the unencrypted timeout ends the link if nothing does. bt_unpair here dropped
 * the link at once, so a kicked phone never got that far and came back every 2 s (10-05). */
static void security_changed(struct bt_conn *conn, bt_security_t level, enum bt_security_err err)
{
    LOG_INF("security level %d, err %d", level, err);
    lr_log(LR_SECURITY, level, err, 0);
    struct bond_find f = {.addr = bt_conn_get_dst(conn)};
    if (err == BT_SECURITY_ERR_PIN_OR_KEY_MISSING && (bt_foreach_bond(BT_ID_DEFAULT, bond_find, &f), f.found)) {
        LOG_WRN("stale bond dropped");
        lr_log(LR_FORGET, 1, 1, 0);
        bt_unpair(BT_ID_DEFAULT, f.addr);
    }
}

/* A link still unencrypted this long after connecting (a phone this recorder no longer knows, anything that connects
 * and never pairs) goes: it would hold the only connection. As long as SMP's own timeout, so a pairing waiting for a
 * person's tap is not cut. */
static void unencrypted_check(struct k_work *work)
{
    struct bt_conn *conn = current_connection;
    if (conn && bt_conn_get_security(conn) < BT_SECURITY_L2) {
        LOG_WRN("still unencrypted after %d s: disconnecting", UNENCRYPTED_S);
        bt_conn_disconnect(conn, BT_HCI_ERR_AUTH_FAIL);
    }
}

static struct bt_conn_auth_info_cb auth_info_cb = {
    .pairing_complete = pairing_complete, .pairing_failed = pairing_failed, .bond_deleted = bond_deleted};

static struct bt_conn_cb _callback_references = {
    .connected = _transport_connected,
    .disconnected = _transport_disconnected,
    .le_param_req = _le_param_req,
    .le_param_updated = _le_param_updated,
    .le_phy_updated = _le_phy_updated,
    .le_data_len_updated = _le_data_length_updated,
    .security_changed = security_changed,
};

//
// Ring Buffer
//

#define RING_BUFFER_HEADER_SIZE 6  // size:u16 (top bit: frames left out before this one), frame number:u32
#define AFTER_GAP 0x80
uint32_t transport_frame_no;  // frames the codec has made since boot, the offline store's clock
static uint32_t tx_frame_no;
static bool tx_after_gap;  // the loaded frame follows frames left out on purpose (unbound, paused)
static bool skipped;       // frames have been left out since the last one queued
// Frames queued by their real size (header + Opus, ~46 B), not in fixed 326 B slots: 8 KB hold ~1.7 s. The pusher no
// longer waits for the card (its batches queue in lr_store.c), so this covers a busy CPU only; 16 KB held a card
// that stalled for most of a second (769 ms seen, 10-04)
static uint8_t tx_queue[8192];
static uint8_t tx_buffer[CODEC_OUTPUT_MAX_BYTES + RING_BUFFER_HEADER_SIZE];
static uint8_t tx_buffer_2[CODEC_OUTPUT_MAX_BYTES + RING_BUFFER_HEADER_SIZE];
static uint32_t tx_buffer_size = 0;
static struct ring_buf ring_buf;
static bool tx_frame_pending = false;

static bool write_to_tx_queue(uint8_t *data, size_t size, uint32_t frame_no)
{
    if (size > CODEC_OUTPUT_MAX_BYTES) {
        return false;
    }
    if (!capturing()) {  // unbound or paused: nothing kept, the frame only counted (broadcast_audio_packets numbered it)
        skipped = true;
        return true;
    }

    // Copy data (TODO: Avoid this copy)
    tx_buffer_2[0] = size & 0xFF;
    tx_buffer_2[1] = ((size >> 8) & 0x7F) | (skipped ? AFTER_GAP : 0);
    sys_put_le32(frame_no, tx_buffer_2 + 2);
    memcpy(tx_buffer_2 + RING_BUFFER_HEADER_SIZE, data, size);

    // Write to ring buffer: whole or not at all
    if (ring_buf_space_get(&ring_buf) < RING_BUFFER_HEADER_SIZE + size) {
        return false;
    }
    ring_buf_put(&ring_buf, tx_buffer_2, RING_BUFFER_HEADER_SIZE + size);
    skipped = false;
    k_sem_give(&pusher_wake_sem);
    return true;
}

static bool load_tx_frame(void)
{
    if (tx_frame_pending) {
        return true;
    }

    if (ring_buf_size_get(&ring_buf) < RING_BUFFER_HEADER_SIZE) {
        return false;
    }
    ring_buf_get(&ring_buf, tx_buffer, RING_BUFFER_HEADER_SIZE);  // a frame is put whole: its data follows
    tx_buffer_size = tx_buffer[0] + ((tx_buffer[1] & 0x7F) << 8);
    tx_after_gap = tx_buffer[1] & AFTER_GAP;
    ring_buf_get(&ring_buf, tx_buffer + RING_BUFFER_HEADER_SIZE, tx_buffer_size);

    tx_frame_no = sys_get_le32(tx_buffer + 2);
    tx_frame_pending = true;
    return true;
}

static void consume_tx_frame(void)
{
    tx_frame_pending = false;
    tx_buffer_size = 0;
}

//
// Pusher
//

// Thread
K_THREAD_STACK_DEFINE(pusher_stack, 4096);
struct k_thread pusher_thread;  // lr_log.c watches its stack
void pusher(void)
{
    k_msleep(500);

    while (1) {
        k_sem_take(&pusher_wake_sem, K_FOREVER);

        // every frame into the record stream (lr_store.c): to the card always, to a phone that follows it (LIVE); the
        // card's own thread writes it, a card that stalls does not hold this one
        while (load_tx_frame()) {
            lr_store_frame(tx_frame_no, tx_buffer + RING_BUFFER_HEADER_SIZE, tx_buffer_size, tx_after_gap);
            consume_tx_frame();
            k_yield();
        }
    }
}
//
// Public functions
//
int bt_off()
{
    // First disconnect any active connections
    if (current_connection != NULL) {
        bt_conn_disconnect(current_connection, BT_HCI_ERR_REMOTE_USER_TERM_CONN);
        bt_conn_unref(current_connection);
        current_connection = NULL;
    }

    // Stop advertising
    int err = bt_le_adv_stop();
    if (err) {
        LOG_ERR("Failed to stop Bluetooth advertising %d", err);
    }

    // Disable Bluetooth
    err = bt_disable();
    if (err) {
        LOG_ERR("Failed to disable Bluetooth %d", err);
    }

    // Turn off other peripherals
    k_mutex_lock(&write_sdcard_mutex, K_FOREVER);
    sd_off();
    k_mutex_unlock(&write_sdcard_mutex);
    mic_off();

    // Ensure all Bluetooth resources are cleaned up
    is_connected = false;
    current_mtu = 0;
    k_sem_give(&pusher_wake_sem);

    return 0;
}
int bt_on()
{
    int err = bt_enable(NULL);
    adv_refresh();
    sd_on();
    mic_on();
    k_sem_give(&pusher_wake_sem);

    return 0;
}

// periodic advertising
int transport_start()
{
    k_mutex_init(&write_sdcard_mutex);

    // Configure callbacks
    bt_conn_cb_register(&_callback_references);
    bt_gatt_cb_register(&gatt_cb);

    // Enable Bluetooth
    int err = bt_enable(NULL);
    if (err) {
        LOG_ERR("Transport bluetooth init failed (err %d)", err);
        return err;
    }
    LOG_INF("Transport bluetooth initialized");
    settings_load();  // the bonds and the identity
    bt_conn_auth_info_cb_register(&auth_info_cb);
    lr_set_name();

    //  Enable button
#ifdef CONFIG_OMI_ENABLE_BUTTON
    register_button_service();
#endif

#ifdef CONFIG_OMI_ENABLE_SPEAKER
    register_speaker_service();
#endif

    // Start advertising
    bt_gatt_service_register(&dfu_service);
    count_bonds();  // and advertising starts with what they say

    int battErr = battery_init();
    if (battErr) {
        LOG_ERR("Battery init failed (err %d)", battErr);
    } else {
        LOG_INF("Battery initialized");
    }
    // from boot, not from the first connection: the scan response's battery is for a phone that has not connected
    k_work_schedule(&battery_work, K_MSEC(100));

    // Start pusher
    ring_buf_init(&ring_buf, sizeof(tx_queue), tx_queue);
    k_thread_create(&pusher_thread,
                    pusher_stack,
                    K_THREAD_STACK_SIZEOF(pusher_stack),
                    (k_thread_entry_t) pusher,
                    NULL,
                    NULL,
                    NULL,
                    K_PRIO_PREEMPT(7),
                    0,
                    K_NO_WAIT);

    return 0;
}

struct bt_conn *get_current_connection()
{
    return current_connection;
}

int broadcast_audio_packets(uint8_t *buffer, size_t size)
{
    int retry_count = 0;
    const int max_retries = 3;
    // numbered once, queued or not: a frame that never makes it is a gap, not a shift (a number per try moved every
    // later frame 10 ms on)
    uint32_t frame_no = transport_frame_no++;

    while (retry_count < max_retries && !write_to_tx_queue(buffer, size, frame_no)) {
        k_sleep(K_MSEC(1));
        retry_count++;
    }

    if (retry_count >= max_retries) {
        LOG_ERR("Failed to write to tx queue after %d retries", max_retries);
        return -1;
    }

    return 0;
}

void accel_off()
{
    gpio_pin_set_dt(&accel_gpio_pin, 0);
}