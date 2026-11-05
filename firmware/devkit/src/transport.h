#ifndef TRANSPORT_H
#define TRANSPORT_H

#include <stdbool.h>
#include <stdint.h>
#include <zephyr/drivers/sensor.h>
typedef struct sensors {

    struct sensor_value a_x;
    struct sensor_value a_y;
    struct sensor_value a_z;
    struct sensor_value g_x;
    struct sensor_value g_y;
    struct sensor_value g_z;
};
/**
 * @brief Initialize the BLE transport logic
 *
 * Initializes the BLE Logic
 *
 * @return 0 if successful, negative errno code if error
 */
int transport_start();
int broadcast_audio_packets(uint8_t *buffer, size_t size);
struct bt_conn *get_current_connection();
int bt_on();
int bt_off();

/* Bound and paused: nothing is recorded while no phone is bonded or while a phone has paused it. */
int lr_bond_count(void);
bool lr_paused(void);
void lr_pause(uint32_t s);  // s seconds (0: resume now)
uint32_t lr_pause_left(void);
extern volatile uint8_t lr_led_paired;  // half-seconds of the green "paired" blinks left

/* CONFIG_LR_POWER_DEBUG: the link dropped and none taken for s seconds (scannable only), then connectable again */
void lr_debug_hold(uint16_t s);
void lr_debug_adv(uint16_t ms);  // advertising while connected or held every ms (0: as released; 0xFFFF: none)

void accel_off();
#endif