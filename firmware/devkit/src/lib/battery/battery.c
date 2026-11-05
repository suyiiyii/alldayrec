/*
 * Copyright 2024 Marcus Alexander Tjomsaas
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#include "battery.h"

#include <zephyr/device.h>
#include <zephyr/devicetree.h>
#include <zephyr/drivers/adc.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <hal/nrf_gpio.h>
#include <hal/nrf_power.h>
LOG_MODULE_REGISTER(battery, LOG_LEVEL_INF);

static const struct device *gpio_battery_dev = DEVICE_DT_GET(DT_NODELABEL(gpio0));
static const struct device *adc_battery_dev = DEVICE_DT_GET(DT_NODELABEL(adc));

static K_MUTEX_DEFINE(battery_mut);

#define GPIO_BATTERY_CHARGE_SPEED 13
// The charger's ~CHG (BQ25101, open drain): low while it charges, open when done or without USB. Its red LED hangs off
// it (3V3, 2.2k, LED, P0.17), so an input reads it with the LED as it is; Omi drove it low as a "charge enable", which
// only kept the LED lit on the battery (~0.65 mA). The pull-up: a bare input costs ~50 uA (Seeed's forum).
#define GPIO_BATTERY_CHG 17
#define GPIO_BATTERY_READ_ENABLE 14
// LR-N4's TP4054 CHRG on P0.29 (open drain, no resistor on the board): low while it charges, a weak pull-down when
// done, open without USB. Pulled up inside only for the read and only with VBUS there: unpowered, the charger would be
// fed through that pin. Neither the XIAO's nor the ProMicro's charger pins exist there (VDDH and AIN0 builds).
#define GPIO_N4_CHRG 29
#define OWN_CHARGER_PINS !(IS_ENABLED(CONFIG_LR_BATTERY_VDDH) || IS_ENABLED(CONFIG_LR_BATTERY_AIN0))

// Change this to a higher number for better averages
// Note that increasing this holds up the thread / ADC for longer.
#define ADC_TOTAL_SAMPLES 10
int16_t sample_buffer[ADC_TOTAL_SAMPLES];

#define ADC_RESOLUTION 12
#define ADC_CHANNEL 7
#ifdef CONFIG_LR_BATTERY_VDDH
#define ADC_PORT SAADC_CH_PSELP_PSELP_VDDHDIV5 // VDDH / 5: the battery feeds VDDH on the nice!nano
#elif defined(CONFIG_LR_BATTERY_AIN0)
#define ADC_PORT SAADC_CH_PSELP_PSELP_AnalogInput0 // AIN0 (P0.02): LR-N4's battery through 1M / 1M
#else
#define ADC_PORT SAADC_CH_PSELP_PSELP_AnalogInput7 // AIN7
#endif
#define ADC_REFERENCE ADC_REF_INTERNAL             // 0.6V
#define ADC_GAIN ADC_GAIN_1_6                      // ADC REFERENCE * 6 = 3.6V

struct adc_channel_cfg channel_7_cfg = {.gain = ADC_GAIN,
                                        .reference = ADC_REFERENCE,
#ifdef CONFIG_LR_BATTERY_AIN0
                                        // 1M / 1M is a 500k source: 40 us covers up to 800k (PS v1.11 SAADC, TACQ)
                                        .acquisition_time = ADC_ACQ_TIME(ADC_ACQ_TIME_MICROSECONDS, 40),
#else
                                        .acquisition_time = ADC_ACQ_TIME_DEFAULT,
#endif
                                        .channel_id = ADC_CHANNEL,
#ifdef CONFIG_ADC_NRFX_SAADC
                                        .input_positive = ADC_PORT
#endif
};

static struct adc_sequence_options options = {
    .extra_samplings = ADC_TOTAL_SAMPLES - 1,
    .interval_us = 500, // Interval between each sample
};

struct adc_sequence sequence = {
    .options = &options,
    .channels = BIT(ADC_CHANNEL),
    .buffer = sample_buffer,
    .buffer_size = sizeof(sample_buffer),
    .resolution = ADC_RESOLUTION,
};

typedef struct {
    uint16_t voltage;
    uint8_t percentage;
} BatteryState;

#define BATTERY_STATES_COUNT 14
// The share of a full charge's recording time left, by the reading, measured on LR-X1-567E's 602030 recording to its
// card: its HEARTBEATs over three runs to empty, 10-05 and 10-06, agree within ~10 minutes below 3.75 V; a full
// charge records ~10.5 h to 3.3 V (of the 10-12 h to empty), the flat stretches (4.00-4.02 V, 3.83-3.85 V) hold hours.
// 0% at 3.3 V: the low-battery cut-off. The old table (Omi's, a straight line) said 50% with 2.5 h of 10.5 left.
BatteryState battery_states[BATTERY_STATES_COUNT] = {
    {4100, 100},
    {4020, 94},
    {4000, 85},
    {3920, 73},
    {3870, 65},
    {3830, 48},
    {3810, 38},
    {3770, 27},
    {3730, 22},
    {3690, 19},
    {3610, 14},
    {3530, 8},
    {3450, 3},
    {3300, 0},
};

static uint8_t is_initialized = false;

static int battery_enable_read()
{
    if (!OWN_CHARGER_PINS) {
        return 0;
    }
    return gpio_pin_set(gpio_battery_dev, GPIO_BATTERY_READ_ENABLE, 1);
}

int battery_set_fast_charge()
{
    if (!OWN_CHARGER_PINS) {
        return 0; // no charger pins
    }
    if (!is_initialized) {
        return -ECANCELED;
    }

    return gpio_pin_set(gpio_battery_dev, GPIO_BATTERY_CHARGE_SPEED, 1); // FAST charge 100mA
}

int battery_set_slow_charge()
{
    if (!OWN_CHARGER_PINS) {
        return 0; // no charger pins
    }
    if (!is_initialized) {
        return -ECANCELED;
    }

    return gpio_pin_set(gpio_battery_dev, GPIO_BATTERY_CHARGE_SPEED, 0); // SLOW charge 50mA
}

int battery_charging()
{
#ifdef CONFIG_LR_BATTERY_AIN0
    if (!nrf_power_usbregstatus_vbusdet_get(NRF_POWER)) {
        return -ENODATA;
    }
    nrf_gpio_cfg_input(NRF_GPIO_PIN_MAP(0, GPIO_N4_CHRG), NRF_GPIO_PIN_PULLUP);
    k_busy_wait(50);
    int level = nrf_gpio_pin_read(NRF_GPIO_PIN_MAP(0, GPIO_N4_CHRG));
    nrf_gpio_cfg_default(NRF_GPIO_PIN_MAP(0, GPIO_N4_CHRG));
    return !level;  // the weak pull-down when done loses to the ~13k pull-up: high
#endif
    if (IS_ENABLED(CONFIG_LR_BATTERY_VDDH)) {
        return -ENOTSUP; // the ProMicro's charger drives its blue LED only (a wire from its CHRG would read it)
    }
    if (!is_initialized) {
        return -ECANCELED;
    }
    return gpio_pin_get(gpio_battery_dev, GPIO_BATTERY_CHG);
}

int battery_get_millivolt(uint16_t *battery_millivolt)
{

    int ret = 0;

    // Voltage divider circuit (Should tune R1 in software if possible)
    const uint16_t R1 = 1037; // Originally 1M ohm, calibrated after measuring actual voltage values. Can happen due to
                              // resistor tolerances, temperature ect..
    const uint16_t R2 = 510;  // 510K ohm

    // ADC measure
    uint16_t adc_vref = adc_ref_internal(adc_battery_dev);
    int adc_mv = 0;

    k_mutex_lock(&battery_mut, K_FOREVER);
    ret |= adc_read(adc_battery_dev, &sequence);

    if (ret) {
        LOG_WRN("ADC read failed (error %d)", ret);
    }

    // Get average sample value.
    for (uint8_t sample = 0; sample < ADC_TOTAL_SAMPLES; sample++) {
        adc_mv += sample_buffer[sample]; // ADC value, not millivolt yet.
    }
    adc_mv /= ADC_TOTAL_SAMPLES;

    // Convert ADC value to millivolts
    ret |= adc_raw_to_millivolts(adc_vref, ADC_GAIN, ADC_RESOLUTION, &adc_mv);

    // Calculate battery voltage.
    *battery_millivolt = IS_ENABLED(CONFIG_LR_BATTERY_VDDH)   ? adc_mv * 5
                         : IS_ENABLED(CONFIG_LR_BATTERY_AIN0) ? adc_mv * 2
                                                              : adc_mv * ((R1 + R2) / R2);
    k_mutex_unlock(&battery_mut);

    LOG_DBG("%d mV", *battery_millivolt);
    return ret;
}

int battery_get_percentage(uint8_t *battery_percentage, uint16_t battery_millivolt)
{
    // Ensure voltage is within bounds
    if (battery_millivolt >= battery_states[0].voltage) {
        *battery_percentage = battery_states[0].percentage;
        LOG_DBG("%d %%", *battery_percentage);
        return 0;
    }
    if (battery_millivolt <= battery_states[BATTERY_STATES_COUNT - 1].voltage) {
        *battery_percentage = battery_states[BATTERY_STATES_COUNT - 1].percentage;
        LOG_DBG("%d %%", *battery_percentage);
        return 0;
    }

    for (uint16_t i = 0; i < BATTERY_STATES_COUNT - 1; i++) {
        // Find the two points battery_millivolt is between
        if (battery_millivolt <= battery_states[i].voltage && battery_millivolt > battery_states[i + 1].voltage) {
            // Linear interpolation
            float voltage_range = (float) (battery_states[i].voltage - battery_states[i + 1].voltage);
            float percentage_range = (float) (battery_states[i].percentage - battery_states[i + 1].percentage);
            float position = (float) (battery_states[i].voltage - battery_millivolt) / voltage_range;

            *battery_percentage = battery_states[i].percentage - (uint8_t) (position * percentage_range);

            LOG_DBG("%d %%", *battery_percentage);
            return 0;
        }
    }
    return -ESPIPE;
}

int battery_init()
{
    int ret = 0;

    // ADC
    if (!device_is_ready(adc_battery_dev)) {
        LOG_ERR("ADC device not found!");
        return -EIO;
    }

    ret |= adc_channel_setup(adc_battery_dev, &channel_7_cfg);

    if (ret) {
        LOG_ERR("ADC setup failed (error %d)", ret);
    }

    if (!OWN_CHARGER_PINS) {
        is_initialized = true;
        return ret;
    }

    // GPIO
    if (!device_is_ready(gpio_battery_dev)) {
        LOG_ERR("GPIO device not found!");
        return -EIO;
    }

    ret |= gpio_pin_configure(gpio_battery_dev, GPIO_BATTERY_CHG, GPIO_INPUT | GPIO_PULL_UP | GPIO_ACTIVE_LOW);
    ret |= gpio_pin_configure(gpio_battery_dev, GPIO_BATTERY_READ_ENABLE, GPIO_OUTPUT | GPIO_ACTIVE_LOW);
    ret |= gpio_pin_configure(gpio_battery_dev, GPIO_BATTERY_CHARGE_SPEED, GPIO_OUTPUT | GPIO_ACTIVE_LOW);

    if (ret) {
        LOG_ERR("GPIO configure failed!");
        return ret;
    }

    if (ret) {
        LOG_ERR("Initialization failed (error %d)", ret);
        return ret;
    }

    is_initialized = true;
    LOG_INF("Initialized");

    ret |= battery_enable_read();
    ret |= battery_set_fast_charge();

    return ret;
}
