/* Board self-test for nRF52840 Pro Micro boards (nice!nano and its clones): flash it onto every new board before our
 * firmware. Commands come over the USB serial port, one per line; each answers with one result line "R {json}" whose
 * "ok" is the board's verdict: info gpio adc rx led clk N dcdc all dfu, and for an LR-P3 (the board wired into a
 * recorder) gpio p3, sd, mic N, sw. selftest.py drives it and adds the checks that need the host (the 32 MHz crystal
 * against the Mac's clock, the board's advertising as the Mac hears it). README.md says what each check proves and its
 * pass mark. */
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

/* The Pro Micro footprint's GPIOs (USB up; left row, right row, the three pads at the bottom edge). Not here: RST
 * (P0.18), the LED (P0.15) and the VCC switch (P0.13), which have parts on them */
#define P(port, n) NRF_GPIO_PIN_MAP(port, n)
static const uint8_t pins[] = {
	P(0, 6), P(0, 8), P(0, 17), P(0, 20), P(0, 22), P(0, 24), P(1, 0), P(0, 11), P(1, 4), P(1, 6),
	P(0, 31), P(0, 29), P(0, 2), P(1, 15), P(1, 13), P(1, 11), P(0, 10), P(0, 9),
	P(1, 1), P(1, 2), P(1, 7),
};
#define LED_PIN P(0, 15)

/* LR-P3: the recorder's parts wired to the Pro Micro, pins as firmware/devkit's lr_promicro.overlay. Only the
 * sd, mic and sw commands use them; "gpio p3" leaves them out, since the parts drive some of them */
#define SD_PWR P(0, 13)  /* the VCC pin's switch (high: on), the SD module's supply */
#define SD_CS P(0, 20)
#define SD_MOSI P(0, 22)
#define SD_SCK P(0, 24)
#define SD_MISO P(1, 0)
#define MIC_DAT P(0, 6)
#define MIC_CLK P(0, 8)
#define MIC_PWR P(0, 17)
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
	printk("R {\"test\":\"gpio\",\"ok\":%d,\"pins\":%d,\"nfc_as_gpio\":%d,\"p3\":%d,%s]}\n", !bad && !shorts,
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

	return mv > e * 95 / 100 && mv < e * 105 / 100;
}

/* VDD from the chip's regulator; VDDH, where USB (through the board's diode) and the battery come in: on USB it reads
 * the USB side; P0.04 (AIN2), the nice!nano v2's battery divider, which a clone may not have */
static void cmd_adc(void)
{
	int vdd = adc_mv(NRF_SAADC_INPUT_VDD);
	int vddh = adc_mv(NRF_SAADC_INPUT_VDDHDIV5) * 5;
	int ain2 = adc_mv(NRF_SAADC_INPUT_AIN2);

	printk("R {\"test\":\"adc\",\"ok\":%d,\"vdd_mv\":%d,\"vdd_expected_mv\":%d,\"vddh_mv\":%d,\"p0_04_mv\":%d}\n",
	       vdd_ok(vdd) && vddh > 4300 && vddh < 5500, vdd, vdd_expected(), vddh, ain2);
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

/* The blue LED, by eye: five blinks */
static void cmd_led(void)
{
	nrf_gpio_cfg_output(LED_PIN);
	for (int i = 0; i < 5; i++) {
		nrf_gpio_pin_set(LED_PIN);
		k_msleep(300);
		nrf_gpio_pin_clear(LED_PIN);
		k_msleep(300);
	}
	nrf_gpio_cfg_default(LED_PIN);
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
static void pdm_start(bool rising);
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
		pdm_start(false);
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
 * power reaching the mic, DAT open or shorted) gives a flat line at full scale or zero */
static int16_t __aligned(4) pdm_buf[2][1600];

/* The mic powered and clocked into pdm_buf[0]; pdm_stop() undoes it */
static void pdm_start(bool rising)
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
	NRF_PDM->MODE = PDM_MODE_OPERATION_Mono << PDM_MODE_OPERATION_Pos |
			(rising ? PDM_MODE_EDGE_LeftRising : PDM_MODE_EDGE_LeftFalling) << PDM_MODE_EDGE_Pos;
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
static void cmd_mic(int secs, bool rising)
{
	int64_t sum = 0, sq = 0;
	int lo = INT16_MAX, hi = INT16_MIN, loudest = 0, count = 0, run = 0, flats = 0, flat = 0, full = 0;
	int16_t prev = 0;
	bool ok = true;

	pdm_start(rising);
	for (int b = 0; ok && b < secs * 10 + 2; b++) {
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
		int64_t bs = 0, bq = 0;
		int bflat = 0, bfull = 0;

		for (int i = 0; i < (int)ARRAY_SIZE(pdm_buf[0]); i++) {
			bs += s[i];
			bq += s[i] * s[i];
			lo = MIN(lo, s[i]);
			hi = MAX(hi, s[i]);
			run = s[i] == prev ? run + 1 : 0;
			prev = s[i];
			flats += run == 160;
			bflat += run >= 160;
			bfull += s[i] == INT16_MAX || s[i] == INT16_MIN;
		}
		flat += bflat;
		full += bfull;
		sum += bs;
		sq += bq;
		count += ARRAY_SIZE(pdm_buf[0]);
		double m = (double)bs / ARRAY_SIZE(pdm_buf[0]);
		int rms = (int)sqrt((double)bq / ARRAY_SIZE(pdm_buf[0]) - m * m);

		loudest = MAX(loudest, rms);
		printk("A %d %d %d %d\n", b - 2, rms, bflat / 16, bfull);
	}
	pdm_stop();
	double mean = count ? (double)sum / count : 0;

	printk("R {\"test\":\"mic\",\"ok\":%d,\"samples\":%d,\"mean\":%d,\"rms\":%d,\"loudest\":%d,\"min\":%d,\"max\":%d,"
	       "\"flats\":%d,\"flat_ms\":%d,\"full\":%d}\n", ok && hi - lo > 2, count, (int)mean,
	       count ? (int)sqrt((double)sq / count - mean * mean) : 0, loudest, lo, hi, flats, flat / 16, full);
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
	       "\"hfxo_ok\":%d,\"hfxo_us\":%u,\"lfxo_ok\":%d,\"lfxo_ms\":%u,\"lfxo_ppb_vs_hfxo\":%d,\"ble\":\"%04X\"}\n",
	       early.hfxo_ok && early.hfxo_us < 5000 && lf_ok, NRF_FICR->DEVICEID[1], NRF_FICR->DEVICEID[0],
	       NRF_FICR->INFO.PART, (int)(var >> 24), (int)(var >> 16) & 0xFF, (int)(var >> 8) & 0xFF, (int)var & 0xFF, NRF_FICR->INFO.PACKAGE,
	       NRF_FICR->INFO.RAM, NRF_FICR->INFO.FLASH, early.resetreas, NRF_UICR->REGOUT0, NRF_UICR->PSELRESET[0],
	       NRF_UICR->NFCPINS, NRF_UICR->APPROTECT, NRF_UICR->NRFFW[0], early.hfxo_ok, early.hfxo_us,
	       early.lfxo_ok, early.lfxo_ms, early.lfxo_ppb,
	       NRF_FICR->DEVICEADDR[0] & 0xFFFF);  /* the recorder's BLE name suffix (transport.c lr_set_name) */
}

static void run(char *line)
{
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
		k_sleep(K_SECONDS(CLAMP(atoi(line + 3), 1, 600)));
		nrf_gpio_cfg_default(SD_PWR);
		printk("R {\"test\":\"vcc\"}\n");
	} else if (!strncmp(line, "mic", 3)) {
		cmd_mic(CLAMP(atoi(line + 3), 1, 120), strchr(line, 'r') != NULL);
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
		printk("R {\"test\":\"?\",\"commands\":\"info gpio [p3] adc rx led clk N dcdc sd [kHz] [m] [r] vcc N mic N [r] sw all dfu\"}\n");
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
