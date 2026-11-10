// Opening the USB serial port at 1200 baud reboots into the UF2 bootloader (the Arduino "1200 bps touch"), so the
// relay is reflashed without a double-tap on reset. GPREGRET 0x57 is the Adafruit bootloader's UF2 magic.
#include <zephyr/drivers/uart/cdc_acm.h>
#include <zephyr/init.h>
#include <zephyr/sys/reboot.h>
#include <hal/nrf_power.h>

static void rate(const struct device *dev, uint32_t baud)
{
	if (baud == 1200) {
		nrf_power_gpregret_set(NRF_POWER, 0, 0x57);
		sys_reboot(SYS_REBOOT_WARM);
	}
}

static int touch_init(void)
{
	return cdc_acm_dte_rate_callback_set(DEVICE_DT_GET(DT_CHOSEN(zephyr_bt_c2h_uart)), rate);
}

SYS_INIT(touch_init, APPLICATION, 0);
