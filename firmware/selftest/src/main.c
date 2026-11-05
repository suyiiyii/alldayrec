/* Board self-test for nRF52840 Pro Micro boards (nice!nano and its clones): flash it onto every new board before our
 * firmware. Commands come over the USB serial port, one per line; each answers with one result line "R {json}" whose
 * "ok" is the board's verdict: info gpio adc rx led clk N dcdc all dfu, and for an LR-P3 (the board wired into a
 * recorder) gpio p3, sd, mic N, sw. selftest.py drives it and adds the checks that need the host (the 32 MHz crystal
 * against the Mac's clock, the board's advertising as the Mac hears it). README.md says what each check proves and its
 * pass mark.
 * Built with LR_N4 (selftest.py build --n4) it is LR-N4's, our own board around a bare nRF52840: its own pins (as
 * firmware/devkit's lr_n4.overlay), gpio over its free balls and its pulled-up lines, mic N s (both mics), imu, chrg. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <zephyr/kernel.h>
#include <zephyr/init.h>
#include <zephyr/device.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/drivers/adc.h>
#include <zephyr/drivers/clock_control.h>
#include <zephyr/drivers/clock_control/nrf_clock_control.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/sys/crc.h>
#include <hal/nrf_gpio.h>
#include <hal/nrf_saadc.h>

#define CYC_PER_US 64  /* the CPU cycle counter: 64 MHz, from the 32 MHz crystal once it runs */
static inline uint32_t cyc(void) { return DWT->CYCCNT; }

/* Clocks, measured before the kernel starts its clocks (it runs on the RC oscillators, prj.conf) */
static struct {
	uint32_t resetreas;
	bool hfxo_ok, lfxo_ok;
	uint32_t hfxo_us, lfxo_ms;
	int32_t lfxo_ppb;  /* the 32.768 kHz crystal against the 32 MHz one */
} early;

static bool wait(const volatile uint32_t *reg, uint32_t mask, bool set, uint32_t us)
{
	for (uint32_t t = cyc(); cyc() - t < us * CYC_PER_US;) {
		if (!(*reg & mask) == !set) {
			return true;
		}
	}
	return false;
}

static int early_clocks(void)
{
	early.resetreas = NRF_POWER->RESETREAS;
	NRF_POWER->RESETREAS = 0xFFFFFFFF;
	CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
	DWT->CYCCNT = 0;
	DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;

	/* 32 MHz crystal: (re)start it and time it until stable; the datasheet's figure is well under 1 ms */
	NRF_CLOCK->TASKS_HFCLKSTOP = 1;
	wait(&NRF_CLOCK->HFCLKSTAT, CLOCK_HFCLKSTAT_SRC_Msk, false, 10000);
	NRF_CLOCK->EVENTS_HFCLKSTARTED = 0;
	uint32_t t0 = cyc();
	NRF_CLOCK->TASKS_HFCLKSTART = 1;
	early.hfxo_ok = wait(&NRF_CLOCK->EVENTS_HFCLKSTARTED, 1, true, 100000);
	early.hfxo_us = (cyc() - t0) / CYC_PER_US;

	/* 32.768 kHz crystal: the source can change only while the clock is stopped; typically stable in 0.25 s */
	NRF_CLOCK->TASKS_LFCLKSTOP = 1;
	wait(&NRF_CLOCK->LFCLKSTAT, CLOCK_LFCLKSTAT_STATE_Msk, false, 10000);
	NRF_CLOCK->LFCLKSRC = CLOCK_LFCLKSRC_SRC_Xtal << CLOCK_LFCLKSRC_SRC_Pos;
	NRF_CLOCK->EVENTS_LFCLKSTARTED = 0;
	t0 = cyc();
	NRF_CLOCK->TASKS_LFCLKSTART = 1;
	early.lfxo_ok = wait(&NRF_CLOCK->EVENTS_LFCLKSTARTED, 1, true, 2000000);
	early.lfxo_ms = (cyc() - t0) / CYC_PER_US / 1000;
	if (early.lfxo_ok && early.hfxo_ok) {
		/* 65536 RTC ticks (2 s) on the crystal take exactly 128e6 CPU cycles when both crystals are exact; interrupts
		 * are not running yet, so the edges are timed to a cycle */
		NRF_RTC2->PRESCALER = 0;
		NRF_RTC2->TASKS_CLEAR = 1;
		NRF_RTC2->TASKS_START = 1;
		uint32_t c = NRF_RTC2->COUNTER;
		while (NRF_RTC2->COUNTER == c) {
		}
		uint32_t a = cyc(), c0 = NRF_RTC2->COUNTER;
		while (NRF_RTC2->COUNTER - c0 < 65536) {
		}
		uint32_t dt = cyc() - a;
		NRF_RTC2->TASKS_STOP = 1;
		early.lfxo_ppb = (int32_t)((128000000LL - dt) * 1000000000LL / dt);
	}

	/* Hand both clocks back stopped; the kernel starts them its own way */
	NRF_CLOCK->TASKS_LFCLKSTOP = 1;
	wait(&NRF_CLOCK->LFCLKSTAT, CLOCK_LFCLKSTAT_STATE_Msk, false, 10000);
	NRF_CLOCK->LFCLKSRC = CLOCK_LFCLKSRC_SRC_RC << CLOCK_LFCLKSRC_SRC_Pos;
	NRF_CLOCK->EVENTS_LFCLKSTARTED = 0;
	NRF_CLOCK->TASKS_HFCLKSTOP = 1;
	NRF_CLOCK->EVENTS_HFCLKSTARTED = 0;
	return 0;
}
SYS_INIT(early_clocks, PRE_KERNEL_1, 0);

#define P(port, n) NRF_GPIO_PIN_MAP(port, n)
#ifdef LR_N4
/* LR-N4's GPIO balls with nothing on them (the hardware's netlist): only these are pulled and driven freely. The
 * others are XL1/XL2 (P0.00/P0.01), RESET (P0.18) and the parts below */
static const uint8_t pins[] = {
	P(0, 3), P(0, 4), P(0, 5), P(0, 7), P(0, 9), P(0, 10), P(0, 13), P(0, 14), P(0, 16), P(0, 17),
	P(0, 21), P(0, 25), P(0, 26), P(0, 27), P(0, 28), P(0, 30), P(1, 1), P(1, 2), P(1, 3), P(1, 4),
	P(1, 5), P(1, 6), P(1, 7), P(1, 9), P(1, 10), P(1, 11), P(1, 12), P(1, 14),
};
#define LED_PIN P(0, 15)  /* D1 yellow-green, through 1k */
#define LED_RED P(1, 13)  /* D1 red, through 1k */
#define SD_PWR P(0, 31)  /* SD_EN: the card's 3.0 V LDO (U3), 100k pull-down */
#define MIC_PWR P(0, 12)  /* the mics' 3.0 V LDO (U5), 100k pull-down */
#define IMU_PWR P(1, 8)  /* the IMU's supply (and its I2C pull-ups'), from the pin as on the XIAO Sense */
#define IMU_SDA P(0, 23)
#define IMU_SCL P(0, 19)
#define IMU_INT P(0, 11)  /* INT1 (push-pull, from the IMU); INT2 is open */
#define CHRG P(0, 29)  /* TP4054 CHRG, open drain, no resistor on the board */
#else
/* The Pro Micro footprint's GPIOs (USB up; left row, right row, the three pads at the bottom edge). Not here: RST
 * (P0.18), the LED (P0.15) and the VCC switch (P0.13), which have parts on them */
static const uint8_t pins[] = {
	P(0, 6), P(0, 8), P(0, 17), P(0, 20), P(0, 22), P(0, 24), P(1, 0), P(0, 11), P(1, 4), P(1, 6),
	P(0, 31), P(0, 29), P(0, 2), P(1, 15), P(1, 13), P(1, 11), P(0, 10), P(0, 9),
	P(1, 1), P(1, 2), P(1, 7),
};
#define LED_PIN P(0, 15)
#define SD_PWR P(0, 13)  /* the VCC pin's switch (high: on), the SD module's supply */
#define MIC_PWR P(0, 17)
#endif

/* LR-P3 (and LR-N4): the recorder's parts wired to the Pro Micro, pins as firmware/devkit's lr_promicro.overlay. Only
 * the sd, mic and sw commands use them; "gpio p3" leaves them out, since the parts drive some of them */
#define SD_CS P(0, 20)
#define SD_MOSI P(0, 22)
#define SD_SCK P(0, 24)
#define SD_MISO P(1, 0)
#define MIC_DAT P(0, 6)
#define MIC_CLK P(0, 8)
#define SWITCH P(1, 15)  /* to GND when on */
static const uint8_t p3_pins[] = {SD_CS, SD_MOSI, SD_SCK, SD_MISO, MIC_DAT, MIC_CLK, MIC_PWR, SWITCH};
static bool p3;  /* gpio skips p3_pins */

static char *pin_name(uint8_t p, char *buf)
{
	sprintf(buf, "P%u.%02u", p >> 5, p & 31);
	return buf;
}

/* Left out of the gpio test: P0.09/P0.10 while they are NFC antenna pins (unless UICR NFCPINS says GPIO), and with
 * "gpio p3" the pins wired to LR-P3's parts */
static bool skip(uint8_t p)
{
	if ((p == P(0, 9) || p == P(0, 10)) && (NRF_UICR->NFCPINS & UICR_NFCPINS_PROTECT_Msk)) {
		return true;
	}
	ARRAY_FOR_EACH(p3_pins, i) {
		if (p3 && p == p3_pins[i]) {
			return true;
		}
	}
	return false;
}

static void pins_input(nrf_gpio_pin_pull_t pull)
{
	ARRAY_FOR_EACH(pins, i) {
		if (!skip(pins[i])) {
			nrf_gpio_cfg_input(pins[i], pull);
		}
	}
}

#ifdef LR_N4
/* The card's, the mics' and the IMU's supplies on (the IMU's at the XIAO Sense's drive), or off: the LDO enables
 * released to their pull-downs, the IMU's pin low */
static void domains(bool on)
{
	if (on) {
		nrf_gpio_pin_set(SD_PWR);
		nrf_gpio_cfg_output(SD_PWR);
		nrf_gpio_pin_set(MIC_PWR);
		nrf_gpio_cfg_output(MIC_PWR);
	} else {
		nrf_gpio_cfg_default(SD_PWR);
		nrf_gpio_cfg_default(MIC_PWR);
	}
	nrf_gpio_pin_write(IMU_PWR, on);
	nrf_gpio_cfg(IMU_PWR, NRF_GPIO_PIN_DIR_OUTPUT, NRF_GPIO_PIN_INPUT_DISCONNECT, NRF_GPIO_PIN_NOPULL, NRF_GPIO_PIN_S0H1,
		     NRF_GPIO_PIN_NOSENSE);
	k_msleep(on ? 50 : 10);
}

/* LR-N4's lines that a powered part pulls up: SD CS, MOSI, MISO (10k to the card's 3.0 V), IMU SDA, SCL (4.7k to its
 * supply). With the three supplies on and no part driving (the card not selected, the IMU not addressed, the mics
 * not clocked) each must read high by itself, else it is tied low. Then each driven low in turn: another that follows
 * is shorted to it, among these, the free balls (pulled up inside meanwhile) and SD SCK, MIC CLK and DAT (pulled up
 * inside, counted only if they read high before). Never driven high, so never against a part's output, and nothing
 * is pulled or driven while the supplies are off: a part without power would be fed through its pins */
static const uint8_t pulled[] = {SD_CS, SD_MOSI, SD_MISO, IMU_SDA, IMU_SCL};
static const uint8_t watched[] = {SD_SCK, MIC_CLK, MIC_DAT};

static int gpio_lines(char *out, int size, int *bad, int *shorts)
{
	char a[8], b[8];
	int n = 0, nb = 0, ns = 0;
	bool rest[ARRAY_SIZE(watched)];

	domains(true);
	ARRAY_FOR_EACH(pulled, i) {
		nrf_gpio_cfg_input(pulled[i], NRF_GPIO_PIN_NOPULL);
	}
	ARRAY_FOR_EACH(watched, i) {
		nrf_gpio_cfg_input(watched[i], NRF_GPIO_PIN_PULLUP);
	}
	pins_input(NRF_GPIO_PIN_PULLUP);
	k_busy_wait(200);
	ARRAY_FOR_EACH(watched, i) {
		rest[i] = nrf_gpio_pin_read(watched[i]);
	}
	n += snprintf(out + n, size - n, ",\"lines_stuck\":[");
	ARRAY_FOR_EACH(pulled, i) {
		if (!nrf_gpio_pin_read(pulled[i])) {
			n += snprintf(out + n, size - n, "%s\"%s low\"", nb++ ? "," : "", pin_name(pulled[i], a));
		}
	}
	n += snprintf(out + n, size - n, "],\"lines_shorts\":[");
	ARRAY_FOR_EACH(pulled, i) {
		nrf_gpio_pin_clear(pulled[i]);
		nrf_gpio_cfg_output(pulled[i]);
		k_busy_wait(100);
		ARRAY_FOR_EACH(pulled, j) {
			if (j != i && !nrf_gpio_pin_read(pulled[j]) && n < size - 40) {
				n += snprintf(out + n, size - n, "%s\"%s-%s\"", ns++ ? "," : "", pin_name(pulled[i], a),
					      pin_name(pulled[j], b));
			}
		}
		ARRAY_FOR_EACH(watched, j) {
			if (rest[j] && !nrf_gpio_pin_read(watched[j]) && n < size - 40) {
				n += snprintf(out + n, size - n, "%s\"%s-%s\"", ns++ ? "," : "", pin_name(pulled[i], a),
					      pin_name(watched[j], b));
			}
		}
		ARRAY_FOR_EACH(pins, j) {
			if (!skip(pins[j]) && !nrf_gpio_pin_read(pins[j]) && n < size - 40) {
				n += snprintf(out + n, size - n, "%s\"%s-%s\"", ns++ ? "," : "", pin_name(pulled[i], a),
					      pin_name(pins[j], b));
			}
		}
		nrf_gpio_cfg_input(pulled[i], NRF_GPIO_PIN_NOPULL);
	}
	n += snprintf(out + n, size - n, "],\"watched_low\":[");
	int nl = 0;
	ARRAY_FOR_EACH(watched, i) {
		if (!rest[i]) {
			n += snprintf(out + n, size - n, "%s\"%s\"", nl++ ? "," : "", pin_name(watched[i], a));
		}
	}
	n += snprintf(out + n, size - n, "]");
	ARRAY_FOR_EACH(pulled, i) {
		nrf_gpio_cfg_default(pulled[i]);
	}
	ARRAY_FOR_EACH(watched, i) {
		nrf_gpio_cfg_default(watched[i]);
	}
	pins_input(NRF_GPIO_PIN_NOPULL);
	ARRAY_FOR_EACH(pins, i) {
		nrf_gpio_cfg_default(pins[i]);
	}
	domains(false);
	*bad += nb;
	*shorts += ns;
	return n;
}
#endif

/* Each pin alone: pulled up it must read 1, pulled down 0 (else it is tied to GND or VDD). Then each pin driven low
 * with all others pulled up, and driven high with all others pulled down: another pin that follows it is shorted to
 * it (a solder bridge). A pin with no contact to its pad passes: that needs a wire on the pad. */
static void cmd_gpio(void)
{
	static char out[700];
	char a[8], b[8];
	int n = 0, bad = 0, tested = 0;

	n += snprintf(out + n, sizeof(out) - n, "\"stuck\":[");
	ARRAY_FOR_EACH(pins, i) {
		uint8_t p = pins[i];

		if (skip(p)) {
			continue;
		}
		tested++;
		nrf_gpio_cfg_input(p, NRF_GPIO_PIN_PULLUP);
		k_busy_wait(100);
		bool up = nrf_gpio_pin_read(p);
		nrf_gpio_cfg_input(p, NRF_GPIO_PIN_PULLDOWN);
		k_busy_wait(100);
		bool down = nrf_gpio_pin_read(p);
		nrf_gpio_cfg_default(p);
		if (!up || down) {
			n += snprintf(out + n, sizeof(out) - n, "%s\"%s %s\"", bad++ ? "," : "", pin_name(p, a),
				      !up ? "low" : "high");
		}
	}
	int shorts = 0;
	n += snprintf(out + n, sizeof(out) - n, "],\"shorts\":[");
	for (int level = 0; level < 2; level++) {
		ARRAY_FOR_EACH(pins, i) {
			if (skip(pins[i])) {
				continue;
			}
			pins_input(level ? NRF_GPIO_PIN_PULLDOWN : NRF_GPIO_PIN_PULLUP);
			nrf_gpio_cfg_output(pins[i]);
			nrf_gpio_pin_write(pins[i], level);
			k_busy_wait(100);
			ARRAY_FOR_EACH(pins, j) {
				if (j > i && !skip(pins[j]) && nrf_gpio_pin_read(pins[j]) == (uint32_t)level &&
				    n < (int)sizeof(out) - 40) {
					n += snprintf(out + n, sizeof(out) - n, "%s\"%s-%s\"", shorts++ ? "," : "",
						      pin_name(pins[i], a), pin_name(pins[j], b));
				}
			}
			nrf_gpio_cfg_default(pins[i]);
		}
	}
	ARRAY_FOR_EACH(pins, i) {
		nrf_gpio_cfg_default(pins[i]);
	}
	n += snprintf(out + n, sizeof(out) - n, "]");
#ifdef LR_N4
	n += gpio_lines(out + n, sizeof(out) - n, &bad, &shorts);
#endif
	printk("R {\"test\":\"gpio\",\"ok\":%d,\"pins\":%d,\"nfc_as_gpio\":%d,\"p3\":%d,%s}\n", !bad && !shorts,
	       tested, !(NRF_UICR->NFCPINS & UICR_NFCPINS_PROTECT_Msk), p3, out);
}

static const struct device *const adc = DEVICE_DT_GET(DT_NODELABEL(adc));

/* One SAADC reading in mV: gain 1/6 on the 0.6 V reference reads 0 to 3.6 V */
static int adc_mv(uint8_t input)
{
	struct adc_channel_cfg ch = {
		.gain = ADC_GAIN_1_6,
		.reference = ADC_REF_INTERNAL,
		.acquisition_time = ADC_ACQ_TIME(ADC_ACQ_TIME_MICROSECONDS, 40),
		.input_positive = input,
	};
	int16_t raw;
	struct adc_sequence seq = {
		.channels = BIT(0),
		.buffer = &raw,
		.buffer_size = sizeof(raw),
		.resolution = 12,
		.oversampling = 4,
		.calibrate = true,
	};

	if (adc_channel_setup(adc, &ch) || adc_read(adc, &seq)) {
		return -1;
	}
	int32_t mv = MAX(raw, 0);
	adc_raw_to_millivolts(600, ADC_GAIN_1_6, 12, &mv);
	return mv;
}

/* What UICR REGOUT0 sets VDD to (the chip runs from VDDH: USB or battery), in mV; erased = 1.8 V */
static int vdd_expected(void)
{
	static const int mv[8] = {1800, 2100, 2400, 2700, 3000, 3300, 1800, 1800};

	return mv[NRF_UICR->REGOUT0 & UICR_REGOUT0_VOUT_Msk];
}

static bool vdd_ok(int mv)
{
	int e = vdd_expected();

#ifdef LR_N4
	return mv >= e * 90 / 100 && mv <= e * 105 / 100;  /* REGOUT0 3.0 V: 2.70-3.15 V, the first boards' T-02 */
#else
	return mv > e * 95 / 100 && mv < e * 105 / 100;
#endif
}

/* VDD from the chip's regulator; VDDH, where USB (through the board's diode) and the battery come in: on USB it reads
 * the USB side; P0.04 (AIN2), the nice!nano v2's battery divider, which a clone may not have */
static void cmd_adc(void)
{
	int vdd = adc_mv(NRF_SAADC_INPUT_VDD);
	int vddh = adc_mv(NRF_SAADC_INPUT_VDDHDIV5) * 5;
	int ain2 = adc_mv(NRF_SAADC_INPUT_AIN2);
#ifdef LR_N4
	int vbat = adc_mv(NRF_SAADC_INPUT_AIN0) * 2;  /* LR-N4: the battery through 1M / 1M on P0.02; VDDH is the system rail */
#else
	int vbat = -1;
#endif

	printk("R {\"test\":\"adc\",\"ok\":%d,\"vdd_mv\":%d,\"vdd_expected_mv\":%d,\"vddh_mv\":%d,\"p0_04_mv\":%d,"
	       "\"vbat_mv\":%d}\n", vdd_ok(vdd) && vddh > 4300 && vddh < 5500, vdd, vdd_expected(), vddh, ain2, vbat);
}

static bt_addr_le_t seen[64];
static int nseen;
static int8_t best_rssi;

static void scan_cb(const bt_addr_le_t *addr, int8_t rssi, uint8_t type, struct net_buf_simple *ad)
{
	best_rssi = MAX(best_rssi, rssi);
	for (int i = 0; i < nseen; i++) {
		if (bt_addr_le_eq(&seen[i], addr)) {
			return;
		}
	}
	if (nseen < (int)ARRAY_SIZE(seen)) {
		bt_addr_le_copy(&seen[nseen++], addr);
	}
}

/* The radio's receive side: 5 s of passive scanning must hear someone's advertising (phones and laptops nearby) */
static void cmd_rx(void)
{
	nseen = 0;
	best_rssi = -127;
	int err = bt_le_scan_start(BT_LE_SCAN_PASSIVE, scan_cb);

	k_sleep(K_SECONDS(5));
	bt_le_scan_stop();
	printk("R {\"test\":\"rx\",\"ok\":%d,\"err\":%d,\"devices\":%d,\"best_rssi\":%d}\n",
	       !err && nseen > 0 && best_rssi >= -90, err, nseen, best_rssi);
}

/* The blue LED, by eye: five blinks (LR-N4: yellow-green and red in turn, five each) */
static void blink(uint8_t pin)
{
	nrf_gpio_cfg_output(pin);
	nrf_gpio_pin_set(pin);
	k_msleep(300);
	nrf_gpio_pin_clear(pin);
	k_msleep(300);
	nrf_gpio_cfg_default(pin);
}

static void cmd_led(void)
{
	for (int i = 0; i < 5; i++) {
		blink(LED_PIN);
#ifdef LR_N4
		blink(LED_RED);
#endif
	}
	printk("R {\"test\":\"led\",\"blinks\":5}\n");
}

/* Hold the 32 MHz crystal through the kernel's HF clock request (the kernel otherwise runs on the RC), or let it go */
static void hfxo(bool on)
{
	struct onoff_manager *hf = z_nrf_clock_control_get_onoff(CLOCK_CONTROL_NRF_SUBSYS_HF);
	static struct onoff_client cli;
	int res = -1;

	if (!on) {
		onoff_release(hf);
		return;
	}
	sys_notify_init_spinwait(&cli.notify);
	onoff_request(hf, &cli);
	while (sys_notify_fetch_result(&cli.notify, &res) == -EAGAIN) {
	}
}

/* The 32 MHz crystal's own count (TIMER4 at 16 MHz) for secs seconds, 20 lines "T i ticks" a second; the host fits
 * it against its clock */
static void cmd_clk(int secs)
{
	hfxo(true);
	NRF_TIMER4->MODE = TIMER_MODE_MODE_Timer;
	NRF_TIMER4->BITMODE = TIMER_BITMODE_BITMODE_32Bit;
	NRF_TIMER4->PRESCALER = 0;
	NRF_TIMER4->TASKS_CLEAR = 1;
	NRF_TIMER4->TASKS_START = 1;
	uint64_t hi = 0;
	uint32_t last = 0;
	for (int i = 0; i < secs * 20; i++) {
		NRF_TIMER4->TASKS_CAPTURE[0] = 1;
		uint32_t now = NRF_TIMER4->CC[0];

		if (now < last) {
			hi += 1ULL << 32;
		}
		last = now;
		printk("T %d %llu\n", i, hi | now);
		k_msleep(50);
	}
	NRF_TIMER4->TASKS_STOP = 1;
	hfxo(false);
	printk("R {\"test\":\"clk\",\"hz\":16000000,\"lines\":%d}\n", secs * 20);
}

/* LR-P3's SD card on the legacy SPI peripheral, as our firmware runs it (lr_sd.overlay), one byte at a time */
static bool sd_hc;  /* block addressing (SDHC/SDXC), else byte addresses */
static int sd_crc_errs;  /* blocks read whose CRC did not match: bits lost on the wires */
static uint8_t sd_resp;  /* the last write's data response: 0x05 accepted, 0x0B CRC error, 0x0D write error */
static uint32_t sd_busy_ms;  /* the longest a write kept the card busy */
static int sd_first_bad = -1;  /* the first failed read's R1 << 8 | token: R1 0xFF, the card not answering (reset by a
				* supply dip, or CS/SCK/MISO open); garbled values, bits lost on the wires */

static uint8_t spi(uint8_t tx)
{
	NRF_SPI2->EVENTS_READY = 0;
	NRF_SPI2->TXD = tx;
	while (!NRF_SPI2->EVENTS_READY) {
	}
	return NRF_SPI2->RXD;
}

/* A command and its R1 (bit 7 clear when it comes; 0xFF: no answer). SPI mode checks the CRC of CMD0 and CMD8 only */
static uint8_t sd_cmd(uint8_t cmd, uint32_t arg)
{
	spi(0xFF);
	spi(0x40 | cmd);
	for (int i = 24; i >= 0; i -= 8) {
		spi(arg >> i);
	}
	spi(cmd == 0 ? 0x95 : cmd == 8 ? 0x87 : 0x01);
	uint8_t r1 = 0xFF;

	for (int i = 0; i < 10 && r1 & 0x80; i++) {
		r1 = spi(0xFF);
	}
	return r1;
}

/* After a read command's R1 0: the data token within 200 ms, n bytes, their CRC */
static uint8_t sd_tok;  /* the last data token (0xFE; 0x0X: an error token) */

static bool sd_data(uint8_t *buf, int n)
{
	uint8_t t = 0xFF;

	for (int64_t end = k_uptime_get() + 200; t == 0xFF && k_uptime_get() < end;) {
		t = spi(0xFF);
	}
	sd_tok = t;
	if (t != 0xFE) {
		return false;
	}
	for (int i = 0; i < n; i++) {
		buf[i] = spi(0xFF);
	}
	uint16_t crc = spi(0xFF) << 8;

	crc |= spi(0xFF);
	if (crc != crc16_itu_t(0, buf, n)) {
		sd_crc_errs++;
		return false;
	}
	return true;
}

static bool sd_read(uint32_t sector, uint8_t *buf)
{
	uint8_t r1 = sd_cmd(17, sd_hc ? sector : sector * 512);

	if (!r1 && sd_data(buf, 512)) {
		return true;
	}
	if (sd_first_bad < 0) {
		sd_first_bad = r1 << 8 | sd_tok;
	}
	return false;
}

/* The data token, the sector, its CRC; then the card's data response and busy (MISO low) until programmed, up to 1 s
 * (the spec allows 500 ms, Zephyr's driver waits longer) */
static bool sd_write(uint32_t sector, const uint8_t *buf)
{
	if (sd_cmd(24, sd_hc ? sector : sector * 512)) {
		return false;
	}
	uint16_t crc = crc16_itu_t(0, buf, 512);

	spi(0xFF);
	spi(0xFE);
	for (int i = 0; i < 512; i++) {
		spi(buf[i]);
	}
	spi(crc >> 8);
	spi(crc);
	sd_resp = spi(0xFF) & 0x1F;
	if (sd_resp != 0x05) {
		return false;
	}
	uint8_t r = 0;
	int64_t t = k_uptime_get();

	while (r != 0xFF && k_uptime_get() < t + 1000) {
		r = spi(0xFF);
	}
	sd_busy_ms = MAX(sd_busy_ms, k_uptime_get() - t);
	return r == 0xFF;
}

/* The card's size from its CSD register, in 512-byte sectors */
static uint32_t csd_sectors(const uint8_t *csd)
{
	if (csd[0] >> 6 == 1) {  /* CSD 2.0: SDHC/SDXC, (C_SIZE + 1) x 512 KB */
		return (((uint32_t)(csd[7] & 0x3F) << 16 | csd[8] << 8 | csd[9]) + 1) * 1024;
	}
	uint32_t c_size = (csd[6] & 3) << 10 | csd[7] << 2 | csd[8] >> 6;
	uint32_t mult = (csd[9] & 3) << 1 | csd[10] >> 7;

	return (c_size + 1) << (mult + 2 + (csd[5] & 0xF) - 9);
}

/* LR-P3's SD card (the SD module on the VCC pin): power it up from off, start it in SPI mode (CMD0, CMD8, ACMD41,
 * CMD58) at 250 kHz, read its size (CMD9); then at khz, on its last sector write a pattern, read it back and write the
 * old contents back (files stay as they were; "restored" 0 if that failed), and read the first 1 MB to count blocks
 * that fail (CRC errors: bits lost on the wires), stopping after 10 s or 20 failures. "fail" names the first step that
 * went wrong */
#define SD_READS 2048
enum { MIC_FALLING, MIC_RISING, MIC_STEREO };
static void pdm_start(int mode);
static void pdm_stop(void);

/* read_only: no write test, reads only (a card with recordings on a board whose wiring is in doubt). with_mic: the
 * mic powered and clocked meanwhile, as when recording (its 1.28 MHz clock runs beside the SD wires) */
static void cmd_sd(int khz, bool with_mic, bool read_only)
{
	static uint8_t old[512], pat[512], back[512];
	const char *fail = "";
	int restored = -1, reads = 0, read_errs = 0;
	uint32_t read_ms = 0;
	uint8_t idle = 0xFF, r0 = 0xFF, r8 = 0xFF, r41 = 0xFF, r9 = 0xFF, csd[16];

	sd_crc_errs = sd_resp = sd_busy_ms = 0;
	sd_first_bad = -1;
	sd_tok = 0xFF;
	uint32_t ocr = 0, r7 = 0, sectors = 0, init_ms = 0, w_us = 0, r_us = 0;
	int part = -1;
	bool mbr = false, saved = false;

	/* VCC off long enough to drain, with every SD pin disconnected (a pin held high would feed the card through its
	 * protection diodes), then on */
	nrf_gpio_cfg_output(SD_PWR);
	nrf_gpio_pin_clear(SD_PWR);
	k_msleep(300);
	nrf_gpio_pin_set(SD_PWR);
	k_msleep(50);
	if (with_mic) {
		pdm_start(MIC_FALLING);
	}
	nrf_gpio_pin_set(SD_CS);
	nrf_gpio_cfg_output(SD_CS);
	nrf_gpio_pin_clear(SD_SCK);
	nrf_gpio_cfg_output(SD_SCK);
	nrf_gpio_pin_set(SD_MOSI);
	nrf_gpio_cfg_output(SD_MOSI);
	nrf_gpio_cfg_input(SD_MISO, NRF_GPIO_PIN_PULLUP);  /* nobody answering reads 0xFF */
	NRF_SPI2->PSEL.SCK = SD_SCK;
	NRF_SPI2->PSEL.MOSI = SD_MOSI;
	NRF_SPI2->PSEL.MISO = SD_MISO;
	NRF_SPI2->CONFIG = 0;  /* mode 0, MSB first */
	NRF_SPI2->FREQUENCY = SPI_FREQUENCY_FREQUENCY_K250;  /* start-up wants 100-400 kHz */
	NRF_SPI2->ENABLE = SPI_ENABLE_ENABLE_Enabled;

	int64_t t0 = k_uptime_get();

	for (int i = 0; i < 10; i++) {  /* 80 clocks with CS high */
		idle &= spi(0xFF);
	}
	nrf_gpio_pin_clear(SD_CS);
	for (int i = 0; i < 10 && r0 != 0x01; i++) {
		r0 = sd_cmd(0, 0);  /* GO_IDLE_STATE, into SPI mode */
	}
	if (r0 != 0x01) {
		fail = "cmd0";
		goto out;
	}
	r8 = sd_cmd(8, 0x1AA);  /* SEND_IF_COND 2.7-3.6 V, echo AA; an SD 1.x card calls it illegal (0x05) */
	for (int i = 0; i < 4 && r8 == 0x01; i++) {
		r7 = r7 << 8 | spi(0xFF);
	}
	if (r8 == 0x01 && (r7 & 0xFFF) != 0x1AA) {
		fail = "cmd8";
		goto out;
	}
	for (int64_t end = k_uptime_get() + 1000; r41 && k_uptime_get() < end;) {
		sd_cmd(55, 0);
		r41 = sd_cmd(41, r8 == 0x01 ? BIT(30) : 0);  /* SD_SEND_OP_COND (HCS on a 2.0 card) until out of idle */
	}
	if (r41) {
		fail = "acmd41";
		goto out;
	}
	init_ms = k_uptime_get() - t0;
	if (sd_cmd(58, 0)) {  /* READ_OCR: CCS says block addressing */
		fail = "cmd58";
		goto out;
	}
	for (int i = 0; i < 4; i++) {
		ocr = ocr << 8 | spi(0xFF);
	}
	sd_hc = ocr & BIT(30);
	r9 = sd_cmd(9, 0);
	if (r9 || !sd_data(csd, 16)) {
		fail = "csd";
		goto out;
	}
	sectors = csd_sectors(csd);
	NRF_SPI2->FREQUENCY = khz >= 8000 ? SPI_FREQUENCY_FREQUENCY_M8 : khz >= 4000 ? SPI_FREQUENCY_FREQUENCY_M4 :
			      khz >= 2000 ? SPI_FREQUENCY_FREQUENCY_M2 : khz >= 1000 ? SPI_FREQUENCY_FREQUENCY_M1 :
			      SPI_FREQUENCY_FREQUENCY_K250;
	if (!sd_read(0, back)) {
		fail = "read";
		goto out;
	}
	mbr = back[510] == 0x55 && back[511] == 0xAA;
	part = back[0x1C2];  /* with an MBR, the first partition's type: 0x0B/0x0C FAT32, 0x07 exFAT */
	if (read_only) {  /* a card someone's recordings are on, its link in doubt: reads only */
		goto out;
	}
	if (!sd_read(sectors - 1, old)) {
		fail = "read";
		goto out;
	}
	saved = true;
	for (int i = 0; i < 512; i++) {
		pat[i] = old[i] ^ (uint8_t)(0x5A + i);  /* differs from the old contents in every byte */
	}
	uint32_t c = cyc();

	if (!sd_write(sectors - 1, pat)) {
		fail = "write";
		goto out;
	}
	w_us = (cyc() - c) / CYC_PER_US;
	c = cyc();
	if (!sd_read(sectors - 1, back)) {
		fail = "readback";
		goto out;
	}
	r_us = (cyc() - c) / CYC_PER_US;
	if (memcmp(back, pat, 512)) {
		fail = "verify";
	}
out:
	if (saved) {
		restored = 0;
		for (int i = 0; i < 3 && !restored; i++) {
			restored = sd_write(sectors - 1, old) && sd_read(sectors - 1, back) && !memcmp(back, old, 512);
		}
	}
	if (!*fail) {
		int64_t t = k_uptime_get();

		for (int i = 0; i < SD_READS && read_errs < 20 && k_uptime_get() < t + 10000; i++, reads++) {
			read_errs += !sd_read(i, back);
		}
		read_ms = k_uptime_get() - t;
	}
	nrf_gpio_pin_set(SD_CS);
	spi(0xFF);
	NRF_SPI2->ENABLE = 0;
	NRF_SPI2->PSEL.SCK = NRF_SPI2->PSEL.MOSI = NRF_SPI2->PSEL.MISO = 0xFFFFFFFF;
	nrf_gpio_cfg_default(SD_CS);
	nrf_gpio_cfg_default(SD_SCK);
	nrf_gpio_cfg_default(SD_MOSI);
	nrf_gpio_cfg_default(SD_MISO);
	nrf_gpio_cfg_default(SD_PWR);
	if (with_mic) {
		pdm_stop();
	}
	printk("R {\"test\":\"sd\",\"mic\":%d,\"ok\":%d,\"fail\":\"%s\",\"idle\":\"0x%02X\",\"cmd0\":\"0x%02X\",\"cmd8\":\"0x%02X\","
	       "\"acmd41\":\"0x%02X\",\"ocr\":\"0x%08X\",\"cmd9\":\"0x%02X\",\"init_ms\":%u,\"sectors\":%u,\"mbr\":%d,\"part\":\"0x%02X\","
	       "\"write_us\":%u,\"read_us\":%u,\"resp\":\"0x%02X\",\"busy_ms\":%u,\"restored\":%d,\"reads\":%d,"
	       "\"read_errs\":%d,\"crc_errs\":%d,\"read_ms\":%u,\"first_bad\":\"0x%04X\"}\n", with_mic, !*fail && restored == 1 && !read_errs, fail, idle, r0, r8,
	       r41, ocr, r9, init_ms, sectors, mbr, part, w_us, r_us, sd_resp, sd_busy_ms, restored, reads,
	       read_errs, sd_crc_errs, read_ms, sd_first_bad & 0xFFFF);
}

/* An EasyDMA event within 1 s */
static bool event(volatile uint32_t *ev)
{
	for (int i = 0; i < 1000 && !*ev; i++) {
		k_msleep(1);
	}
	return *ev;
}

/* LR-P3's PDM mic as our firmware runs it (mic.c): powered from P0.17, 1.28 MHz clock, mono on the falling edge (its
 * SEL is tied to 3V; "mic N r": the rising edge, to tell which half of the clock the mic drives), ratio 80 (16 kHz),
 * gain 0x28. secs seconds after 200 ms of start-up; each 100 ms block's level
 * on an "A i rms" line, then the whole: DC (mean), AC RMS, the loudest block, min and max. A stuck DAT (no clock or no
 * power reaching the mic, DAT open or shorted) gives a flat line at full scale or zero.
 * LR-N4 (MIC_PWR P0.12, its LDO): MK1 drives the half read on the falling edge, MK2 the rising one; "mic N s" reads
 * both (stereo: left MK1, right MK2), 50 ms blocks, "A i" then rms flat_ms full for each, and each one's whole */
static int16_t __aligned(4) pdm_buf[2][1600];

/* The mic powered and clocked into pdm_buf[0]; pdm_stop() undoes it */
static void pdm_start(int mode)
{
	hfxo(true);  /* the PDM clock is divided from HFCLK */
	nrf_gpio_pin_set(MIC_PWR);
	nrf_gpio_cfg_output(MIC_PWR);
	nrf_gpio_pin_clear(MIC_CLK);
	nrf_gpio_cfg_output(MIC_CLK);
	nrf_gpio_cfg_input(MIC_DAT, NRF_GPIO_PIN_NOPULL);
	NRF_PDM->PSEL.CLK = MIC_CLK;
	NRF_PDM->PSEL.DIN = MIC_DAT;
	NRF_PDM->PDMCLKCTRL = PDM_PDMCLKCTRL_FREQ_1280K;
	NRF_PDM->RATIO = PDM_RATIO_RATIO_Ratio80;
	NRF_PDM->MODE = (mode == MIC_STEREO ? PDM_MODE_OPERATION_Stereo : PDM_MODE_OPERATION_Mono) << PDM_MODE_OPERATION_Pos |
			(mode == MIC_RISING ? PDM_MODE_EDGE_LeftRising : PDM_MODE_EDGE_LeftFalling) << PDM_MODE_EDGE_Pos;
	NRF_PDM->GAINL = NRF_PDM->GAINR = 0x28;
	NRF_PDM->SAMPLE.MAXCNT = ARRAY_SIZE(pdm_buf[0]);
	NRF_PDM->SAMPLE.PTR = (uint32_t)pdm_buf[0];
	NRF_PDM->EVENTS_STARTED = NRF_PDM->EVENTS_END = NRF_PDM->EVENTS_STOPPED = 0;
	NRF_PDM->ENABLE = 1;
	NRF_PDM->TASKS_START = 1;
}

static void pdm_stop(void)
{
	NRF_PDM->TASKS_STOP = 1;
	event(&NRF_PDM->EVENTS_STOPPED);
	NRF_PDM->ENABLE = 0;
	NRF_PDM->PSEL.CLK = NRF_PDM->PSEL.DIN = 0xFFFFFFFF;
	nrf_gpio_cfg_default(MIC_CLK);
	nrf_gpio_cfg_default(MIC_DAT);
	nrf_gpio_cfg_default(MIC_PWR);
	hfxo(false);
}

/* Dropouts too (08B2 in its box: >10 ms of zeros and full-scale pops in the recordings): a run of 160 identical
 * samples (10 ms; the mic's own noise never repeats that long, a DAT that stops toggling does) and samples at full
 * scale, per block on the "A i rms flat_ms full" line and in all */
struct mic_ch {
	int64_t sum, sq;
	int lo, hi, loudest, run, flats, flat, full;
	int16_t prev;
};

static void mic_json(const struct mic_ch *c, int count)
{
	double mean = count ? (double)c->sum / count : 0;

	printk("\"mean\":%d,\"rms\":%d,\"loudest\":%d,\"min\":%d,\"max\":%d,\"flats\":%d,\"flat_ms\":%d,\"full\":%d",
	       (int)mean, count ? (int)sqrt((double)c->sq / count - mean * mean) : 0, c->loudest, c->lo, c->hi, c->flats,
	       c->flat / 16, c->full);
}

static void cmd_mic(int secs, int mode)
{
	int nch = mode == MIC_STEREO ? 2 : 1, per = ARRAY_SIZE(pdm_buf[0]) / nch;  /* a channel's samples in a block */
	struct mic_ch ch[2] = {{.lo = INT16_MAX, .hi = INT16_MIN}, {.lo = INT16_MAX, .hi = INT16_MIN}};
	int count = 0;
	bool ok = true;

	pdm_start(mode);
	for (int b = 0; ok && b < secs * 16000 / per + 2; b++) {
		/* once a buffer has started, point at the other one for the block after; it fills while this one is read */
		ok = event(&NRF_PDM->EVENTS_STARTED);
		NRF_PDM->EVENTS_STARTED = 0;
		NRF_PDM->SAMPLE.PTR = (uint32_t)pdm_buf[(b + 1) & 1];
		ok = ok && event(&NRF_PDM->EVENTS_END);
		NRF_PDM->EVENTS_END = 0;
		if (!ok || b < 2) {
			continue;
		}
		const int16_t *s = pdm_buf[b & 1];

		printk("A %d", b - 2);
		for (int k = 0; k < nch; k++) {
			struct mic_ch *c = &ch[k];
			int64_t bs = 0, bq = 0;
			int bflat = 0, bfull = 0;

			for (int i = k; i < (int)ARRAY_SIZE(pdm_buf[0]); i += nch) {
				bs += s[i];
				bq += s[i] * s[i];
				c->lo = MIN(c->lo, s[i]);
				c->hi = MAX(c->hi, s[i]);
				c->run = s[i] == c->prev ? c->run + 1 : 0;
				c->prev = s[i];
				c->flats += c->run == 160;
				bflat += c->run >= 160;
				bfull += s[i] == INT16_MAX || s[i] == INT16_MIN;
			}
			c->flat += bflat;
			c->full += bfull;
			c->sum += bs;
			c->sq += bq;
			double m = (double)bs / per;
			int rms = (int)sqrt((double)bq / per - m * m);

			c->loudest = MAX(c->loudest, rms);
			printk(" %d %d %d", rms, bflat / 16, bfull);
		}
		printk("\n");
		count += per;
	}
	pdm_stop();
	if (nch == 1) {
		printk("R {\"test\":\"mic\",\"ok\":%d,\"samples\":%d,", ok && ch[0].hi - ch[0].lo > 2, count);
		mic_json(&ch[0], count);
		printk("}\n");
		return;
	}
	printk("R {\"test\":\"mic\",\"stereo\":1,\"ok\":%d,\"samples\":%d", ok && ch[0].hi - ch[0].lo > 2 &&
	       ch[1].hi - ch[1].lo > 2, count);
	for (int k = 0; k < 2; k++) {
		printk(",\"%s\":{\"ok\":%d,", k ? "r" : "l", ok && ch[k].hi - ch[k].lo > 2);
		mic_json(&ch[k], count);
		printk("}");
	}
	printk("}\n");
}

/* LR-P3's switch: P1.15 on its pull-up, closed to GND = on; read only, never driven */
static void cmd_sw(void)
{
	nrf_gpio_cfg_input(SWITCH, NRF_GPIO_PIN_PULLUP);
	k_busy_wait(100);
	int on = !nrf_gpio_pin_read(SWITCH);

	nrf_gpio_cfg_default(SWITCH);
	printk("R {\"test\":\"sw\",\"on\":%d}\n", on);
}

#ifdef LR_N4
/* The IMU's I2C on TWIM0 at 100 kHz, one write then a read when rx: false on a NACK or a hang (10 ms) */
static bool twim(const uint8_t *tx, int ntx, uint8_t *rx, int nrx)
{
	NRF_TWIM0->EVENTS_STOPPED = 0;
	NRF_TWIM0->EVENTS_ERROR = 0;
	NRF_TWIM0->ERRORSRC = NRF_TWIM0->ERRORSRC;  /* write 1 to clear */
	NRF_TWIM0->TXD.PTR = (uint32_t)tx;
	NRF_TWIM0->TXD.MAXCNT = ntx;
	NRF_TWIM0->RXD.PTR = (uint32_t)rx;
	NRF_TWIM0->RXD.MAXCNT = nrx;
	NRF_TWIM0->SHORTS = nrx ? TWIM_SHORTS_LASTTX_STARTRX_Msk | TWIM_SHORTS_LASTRX_STOP_Msk : TWIM_SHORTS_LASTTX_STOP_Msk;
	NRF_TWIM0->TASKS_STARTTX = 1;
	for (int i = 0; i < 100 && !NRF_TWIM0->EVENTS_STOPPED && !NRF_TWIM0->EVENTS_ERROR; i++) {
		k_busy_wait(100);
	}
	if (!NRF_TWIM0->EVENTS_STOPPED) {  /* an error (a NACK: no IMU, or not powered) or a hang: stop the bus */
		NRF_TWIM0->TASKS_STOP = 1;
		for (int i = 0; i < 100 && !NRF_TWIM0->EVENTS_STOPPED; i++) {
			k_busy_wait(100);
		}
		return false;
	}
	return !NRF_TWIM0->EVENTS_ERROR && !NRF_TWIM0->ERRORSRC;
}

static uint8_t imu_tx[2], imu_rx[12];  /* EasyDMA: in RAM */

static bool imu_read(uint8_t reg, int n)
{
	imu_tx[0] = reg;
	return twim(imu_tx, 1, imu_rx, n);
}

static bool imu_write(uint8_t reg, uint8_t val)
{
	imu_tx[0] = reg;
	imu_tx[1] = val;
	return twim(imu_tx, 2, NULL, 0);
}

/* LR-N4's IMU (LSM6DS3TR-C at 0x6A): P1.08 up, WHO_AM_I (0x0F) must say 0x6A; then accelerometer and gyro at
 * 104 Hz (CTRL1_XL, CTRL2_G 0x40: +-2 g, 250 dps) with their data-ready on INT1 (INT1_CTRL 0x03), which must rise ten
 * times (INT1 wired), each followed by a read of both (gyro then accelerometer, 0x22-0x2D; the last read counts, past
 * the gyro's ~35 ms start). Lying still: |a| near 1 g, the gyro near 0. Down in the reverse order (R9 review): both
 * to power-down, TWIM off, SDA / SCL / INT1 inputs without pulls, P1.08 low */
static void cmd_imu(void)
{
	uint8_t who = 0;
	int drdy = 0, reads = 0;
	int16_t g[3] = {0}, a[3] = {0};

	nrf_gpio_cfg_input(IMU_INT, NRF_GPIO_PIN_NOPULL);
	nrf_gpio_pin_set(IMU_PWR);
	nrf_gpio_cfg(IMU_PWR, NRF_GPIO_PIN_DIR_OUTPUT, NRF_GPIO_PIN_INPUT_DISCONNECT, NRF_GPIO_PIN_NOPULL, NRF_GPIO_PIN_S0H1,
		     NRF_GPIO_PIN_NOSENSE);
	k_msleep(50);  /* its boot (loading its trim) after power-up */
	nrf_gpio_cfg(IMU_SCL, NRF_GPIO_PIN_DIR_INPUT, NRF_GPIO_PIN_INPUT_CONNECT, NRF_GPIO_PIN_NOPULL, NRF_GPIO_PIN_S0D1,
		     NRF_GPIO_PIN_NOSENSE);
	nrf_gpio_cfg(IMU_SDA, NRF_GPIO_PIN_DIR_INPUT, NRF_GPIO_PIN_INPUT_CONNECT, NRF_GPIO_PIN_NOPULL, NRF_GPIO_PIN_S0D1,
		     NRF_GPIO_PIN_NOSENSE);
	NRF_TWIM0->PSEL.SCL = IMU_SCL;
	NRF_TWIM0->PSEL.SDA = IMU_SDA;
	NRF_TWIM0->FREQUENCY = TWIM_FREQUENCY_FREQUENCY_K100;  /* not 400: errata 219 (lr_n4.overlay) */
	NRF_TWIM0->ADDRESS = 0x6A;
	NRF_TWIM0->ENABLE = TWIM_ENABLE_ENABLE_Enabled;
	bool i2c = imu_read(0x0F, 1);

	who = imu_rx[0];
	if (i2c && who == 0x6A && imu_write(0x12, 0x44) && imu_write(0x0D, 0x03) && imu_write(0x10, 0x40) &&
	    imu_write(0x11, 0x40)) {  /* CTRL3_C: BDU + auto-increment */
		for (int i = 0; i < 10; i++) {
			bool up = false;

			for (int t = 0; t < 50 && !(up = nrf_gpio_pin_read(IMU_INT)); t++) {
				k_msleep(1);
			}
			drdy += up;
			if (imu_read(0x22, 12)) {  /* reading both clears their data-ready */
				reads++;
				for (int k = 0; k < 3; k++) {
					g[k] = imu_rx[2 * k] | imu_rx[2 * k + 1] << 8;
					a[k] = imu_rx[6 + 2 * k] | imu_rx[7 + 2 * k] << 8;
				}
			}
		}
	}
	if (i2c) {
		imu_write(0x10, 0);
		imu_write(0x11, 0);
	}
	NRF_TWIM0->ENABLE = 0;
	NRF_TWIM0->PSEL.SCL = NRF_TWIM0->PSEL.SDA = 0xFFFFFFFF;
	nrf_gpio_cfg_default(IMU_SCL);
	nrf_gpio_cfg_default(IMU_SDA);
	nrf_gpio_cfg_default(IMU_INT);
	nrf_gpio_pin_clear(IMU_PWR);
	int amg = (int)(sqrt((double)a[0] * a[0] + (double)a[1] * a[1] + (double)a[2] * a[2]) * 0.061);  /* 0.061 mg/LSB */
	int gmax = 0;

	for (int k = 0; k < 3; k++) {
		gmax = MAX(gmax, abs(g[k]) * 875 / 100000);  /* 8.75 mdps/LSB, in dps */
	}
	printk("R {\"test\":\"imu\",\"ok\":%d,\"i2c\":%d,\"who_am_i\":\"0x%02X\",\"drdy\":%d,\"reads\":%d,"
	       "\"accel_mg\":[%d,%d,%d],\"accel_abs_mg\":%d,\"gyro_dps_max\":%d}\n",
	       i2c && who == 0x6A && drdy == 10 && reads == 10 && amg > 800 && amg < 1200 && gmax < 25, i2c, who, drdy, reads,
	       a[0] * 61 / 1000, a[1] * 61 / 1000, a[2] * 61 / 1000, amg, gmax);
}

/* LR-N4's charge status: TP4054 CHRG on P0.29 (open drain: low while charging, a weak pull-down once done, open
 * without USB), read on the pull-up inside only while VBUS is there and only for the read: without USB the charger
 * is unpowered and that pull-up would feed it. The battery (AIN0 x2) beside it */
static void cmd_chrg(void)
{
	bool usb = NRF_POWER->USBREGSTATUS & POWER_USBREGSTATUS_VBUSDETECT_Msk;
	int level = -1;

	if (usb) {
		nrf_gpio_cfg_input(CHRG, NRF_GPIO_PIN_PULLUP);
		k_busy_wait(100);
		level = nrf_gpio_pin_read(CHRG);
		nrf_gpio_cfg_default(CHRG);
	}
	printk("R {\"test\":\"chrg\",\"usb\":%d,\"level\":%d,\"vbat_mv\":%d}\n", usb, level,
	       adc_mv(NRF_SAADC_INPUT_AIN0) * 2);
}
#endif

/* The DCDC converters (each needs its inductor; without one the chip browns out and resets, which the host sees as
 * the port going away after an "S" line): REG1 (DCDCEN), then REG0 (DCDCEN0, the high-voltage stage), VDD after each */
static void cmd_dcdc(void)
{
	int v[3];

	v[0] = adc_mv(NRF_SAADC_INPUT_VDD);
	printk("S dcdc reg1\n");
	k_msleep(200);
	NRF_POWER->DCDCEN = 1;
	k_msleep(300);
	v[1] = adc_mv(NRF_SAADC_INPUT_VDD);
	printk("S dcdc reg0\n");
	k_msleep(200);
	NRF_POWER->DCDCEN0 = 1;
	k_msleep(300);
	v[2] = adc_mv(NRF_SAADC_INPUT_VDD);
	NRF_POWER->DCDCEN0 = 0;
	NRF_POWER->DCDCEN = 0;
	printk("R {\"test\":\"dcdc\",\"ok\":%d,\"vdd_ldo_mv\":%d,\"vdd_reg1_mv\":%d,\"vdd_reg0_mv\":%d}\n",
	       vdd_ok(v[0]) && vdd_ok(v[1]) && vdd_ok(v[2]), v[0], v[1], v[2]);
}

static void cmd_info(void)
{
	uint32_t var = NRF_FICR->INFO.VARIANT;
	/* the crystals' accuracy needs the host's clock (selftest.py); here only that both start */
	bool lf_ok = early.lfxo_ok && early.lfxo_ms < 1000;

	printk("R {\"test\":\"info\",\"ok\":%d,\"id\":\"%08X%08X\",\"part\":\"%X\",\"variant\":\"%c%c%c%c\","
	       "\"package\":\"%X\",\"ram_kb\":%u,\"flash_kb\":%u,\"resetreas\":\"0x%X\",\"regout0\":\"0x%X\","
	       "\"pselreset\":\"0x%X\",\"nfcpins\":\"0x%X\",\"approtect\":\"0x%X\",\"bootloader\":\"0x%X\","
	       "\"hfxo_ok\":%d,\"hfxo_us\":%u,\"lfxo_ok\":%d,\"lfxo_ms\":%u,\"lfxo_ppb_vs_hfxo\":%d,\"ble\":\"%04X\","
	       "\"board\":\"%s\",\"approtect_disable\":\"0x%X\"}\n",
	       early.hfxo_ok && early.hfxo_us < 5000 && lf_ok, NRF_FICR->DEVICEID[1], NRF_FICR->DEVICEID[0],
	       NRF_FICR->INFO.PART, (int)(var >> 24), (int)(var >> 16) & 0xFF, (int)(var >> 8) & 0xFF, (int)var & 0xFF, NRF_FICR->INFO.PACKAGE,
	       NRF_FICR->INFO.RAM, NRF_FICR->INFO.FLASH, early.resetreas, NRF_UICR->REGOUT0, NRF_UICR->PSELRESET[0],
	       NRF_UICR->NFCPINS, NRF_UICR->APPROTECT, NRF_UICR->NRFFW[0], early.hfxo_ok, early.hfxo_us,
	       early.lfxo_ok, early.lfxo_ms, early.lfxo_ppb,
	       NRF_FICR->DEVICEADDR[0] & 0xFFFF,  /* the recorder's BLE name suffix (transport.c lr_set_name) */
#ifdef LR_N4
	       /* APPROTECT.DISABLE (0x40000558, PS v1.11 p.70-71), written by SystemInit from UICR APPROTECT on build code Fxx
		* and later (CONFIG_NRF_APPROTECT_USE_UICR): 0x5A there keeps SWD open */
	       "n4", *(volatile uint32_t *)0x40000558);
#else
	       "promicro", 0);
#endif
}

static void run(char *line)
{
#ifdef LR_N4
	/* REGOUT0 not VOUT 4 (3.0 V), e.g. the UICR file left out of the first flash: the bootloader then writes 3.3 V there
	 * itself (it does so when it finds REGOUT0 erased), and pins driven at 3.3 V sit on the 3.0 V card's, mics' and IMU's
	 * VDD + 0.3 limit. Nothing that powers them runs until the UICR is rewritten (ASSEMBLY-SELF 12.2) */
	if ((NRF_UICR->REGOUT0 & 7) != 4 && (!strncmp(line, "gpio", 4) || !strncmp(line, "sd", 2) ||
	    !strncmp(line, "vcc", 3) || !strncmp(line, "mic", 3) || !strcmp(line, "imu") || !strcmp(line, "all"))) {
		printk("R {\"test\":\"%s\",\"ok\":0,\"err\":\"regout0\"}\n", line);
		return;
	}
#endif
	if (!strcmp(line, "info")) {
		cmd_info();
	} else if (!strcmp(line, "gpio") || !strcmp(line, "gpio p3")) {
		p3 = line[4] != 0;
		cmd_gpio();
	} else if (!strcmp(line, "adc")) {
		cmd_adc();
	} else if (!strcmp(line, "rx")) {
		cmd_rx();
	} else if (!strcmp(line, "led")) {
		cmd_led();
	} else if (!strncmp(line, "clk", 3)) {
		cmd_clk(CLAMP(atoi(line + 3), 1, 600));
	} else if (!strcmp(line, "dcdc")) {
		cmd_dcdc();
	} else if (!strncmp(line, "sd", 2)) {
		cmd_sd(line[2] ? atoi(line + 2) : 8000, strchr(line, 'm') != NULL, strchr(line, 'r') != NULL);
	} else if (!strncmp(line, "vcc", 3)) {  /* the SD module's supply on for N s, its pins left open: measure it */
		nrf_gpio_pin_set(SD_PWR);
		nrf_gpio_cfg_output(SD_PWR);
#ifdef LR_N4
		nrf_gpio_pin_set(MIC_PWR);  /* and the mics' LDO: SD_VDD and MIC_VDD */
		nrf_gpio_cfg_output(MIC_PWR);
#endif
		k_sleep(K_SECONDS(CLAMP(atoi(line + 3), 1, 600)));
		nrf_gpio_cfg_default(SD_PWR);
		nrf_gpio_cfg_default(MIC_PWR);
		printk("R {\"test\":\"vcc\"}\n");
	} else if (!strncmp(line, "mic", 3)) {
		cmd_mic(CLAMP(atoi(line + 3), 1, 120), strchr(line, 's') ? MIC_STEREO : strchr(line, 'r') ? MIC_RISING :
			MIC_FALLING);
#ifdef LR_N4
	} else if (!strcmp(line, "imu")) {
		cmd_imu();
	} else if (!strcmp(line, "chrg")) {
		cmd_chrg();
#endif
	} else if (!strcmp(line, "sw")) {
		cmd_sw();
	} else if (!strcmp(line, "all")) {  /* everything the board judges alone, for a plain serial terminal */
		cmd_info();
		cmd_gpio();
		cmd_adc();
		cmd_rx();
		cmd_dcdc();
	} else if (!strcmp(line, "dfu")) {  /* into the UF2 bootloader: Adafruit's GPREGRET magic */
		printk("R {\"test\":\"dfu\"}\n");
		k_msleep(200);
		NRF_POWER->GPREGRET = 0x57;
		NVIC_SystemReset();
	} else {
		printk("R {\"test\":\"?\",\"commands\":\"info gpio [p3] adc rx led clk N dcdc sd [kHz] [m] [r] vcc N mic N [r|s] sw imu chrg all dfu\"}\n");
	}
}

int main(void)
{
	const struct device *con = DEVICE_DT_GET(DT_CHOSEN(zephyr_console));
	static char name[24];

	/* Non-connectable advertising for the host to hear, named after the chip like the USB serial number */
	snprintf(name, sizeof(name), "LRT-%08X%08X", NRF_FICR->DEVICEID[1], NRF_FICR->DEVICEID[0]);
	const struct bt_data ad[] = {
		BT_DATA_BYTES(BT_DATA_FLAGS, BT_LE_AD_NO_BREDR),
		BT_DATA(BT_DATA_NAME_COMPLETE, name, strlen(name)),
	};
	int err = bt_enable(NULL);

	if (!err) {
		err = bt_le_adv_start(BT_LE_ADV_NCONN, ad, ARRAY_SIZE(ad), NULL, 0);
	}
	if (err) {
		printk("R {\"test\":\"bt\",\"ok\":0,\"err\":%d}\n", err);
	}

	char line[32];
	size_t n = 0;

	for (;;) {
		unsigned char c;

		if (uart_poll_in(con, &c)) {
			k_msleep(10);
		} else if (c == '\r' || c == '\n') {
			line[n] = 0;
			if (n) {
				run(line);
			}
			n = 0;
		} else if (n < sizeof(line) - 1) {
			line[n++] = c;
		}
	}
}
