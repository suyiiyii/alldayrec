# /// script
# dependencies = ["pyserial", "bleak"]
# ///
"""Board self-test for nRF52840 Pro Micro boards (README.md). macOS host; boards are named by their USB serial number
(the chip's DEVICEID, the same in the UF2 bootloader and in this firmware).

  python3 selftest.py build              the firmware (NCS v2.7.0, as lr.sh)
  uv run selftest.py flash SERIAL        UF2 onto that board only: its bootloader drive, or this firmware's dfu command
  uv run selftest.py run SERIAL [--clk S] [--json FILE]   every check, a PASS/FAIL report; exit 1 on any FAIL
  uv run selftest.py run SERIAL --p3 [--flip S]           a board wired into an LR-P3: also its SD card, mic and switch
  python3 selftest.py check              the mic verdicts on recorded runs (no board)
"""
import argparse, json, math, os, plistlib, re, shutil, statistics, subprocess, sys, time

D = os.path.dirname(os.path.abspath(__file__))
UF2 = f"{D}/build/zephyr/zephyr.uf2"
PRODUCT = "LR selftest"


def build():
    os.chdir(os.environ.get("NCS", "/opt/nordic/ncs/v2.7.0"))  # a west workspace of NCS v2.7.0
    tc = [] if os.environ.get("ZEPHYR_SDK_INSTALL_DIR") else ["nrfutil", "sdk-manager", "toolchain", "launch",
                                                             "--ncs-version", "v2.7.0", "--"]  # CI: loaded already
    subprocess.run(tc + ["west", "build", "-b", "xiao_ble/nrf52840", "-d", f"{D}/build", D, "--",
                         "-DNCS_TOOLCHAIN_VERSION=NONE", f"-DDTC_OVERLAY_FILE={D}/promicro.overlay"], check=True)


def usb_devices(serial):
    """(product, BSD disk name or None) of each USB device with this serial number, from the IORegistry"""
    out = subprocess.run(["ioreg", "-r", "-c", "IOUSBHostDevice", "-l", "-w0"], capture_output=True, text=True).stdout
    found = []
    for block in re.split(r"\n(?=[ |]*\+-o [^\n]*<class IOUSBHostDevice,)", out):  # devices behind a hub nest in it
        if f'"USB Serial Number" = "{serial}"' in block:
            product = re.search(r'"USB Product Name" = "([^"]*)"', block)
            disk = re.search(r'"BSD Name" = "(disk\d+)"', block)
            found.append((product and product.group(1), disk and disk.group(1)))
    return found


def drive(serial):
    """The mount point of this board's UF2 bootloader drive, checked against its serial number right now"""
    devs = usb_devices(serial)
    disks = [d for _, d in devs if d]
    if len(devs) != 1 or len(disks) != 1:
        return None
    info = plistlib.loads(subprocess.run(["diskutil", "info", "-plist", disks[0]], capture_output=True).stdout)
    mp = info.get("MountPoint")
    return mp if mp and os.path.isfile(f"{mp}/INFO_UF2.TXT") else None


def port(serial):
    from serial.tools import list_ports
    ports = [p for p in list_ports.comports() if p.serial_number == serial and p.product == PRODUCT]
    return ports[0].device if len(ports) == 1 else None


def flash(serial):
    if not drive(serial):
        if not port(serial):
            sys.exit(f"{serial}: neither its UF2 drive nor {PRODUCT} on USB; double-tap RST to enter the bootloader")
        Board(serial).cmd("dfu")  # wait for its reply: closing the port right after the write loses the command
        for _ in range(40):
            time.sleep(0.5)
            if drive(serial):
                break
    mp = drive(serial)  # resolved again right before the copy
    if not mp:
        sys.exit(f"{serial}: no UF2 drive")
    print(f"{serial}: {open(f'{mp}/INFO_UF2.TXT').readline().strip()} at {mp}; copying {UF2}")
    try:
        shutil.copyfile(UF2, f"{mp}/selftest.uf2")
    except OSError:
        pass  # the board reboots as the last block lands
    for _ in range(60):
        time.sleep(0.5)
        if port(serial):
            return print(f"{serial}: {PRODUCT} is up on {port(serial)}")
    sys.exit(f"{serial}: {PRODUCT} did not come up within 30 s")


class Board:
    def __init__(self, serial):
        import serial as pyserial
        p = port(serial)
        if not p:
            sys.exit(f"{serial}: {PRODUCT} not on USB (flash it first)")
        self.s = pyserial.Serial(p, timeout=1)
        self.s.reset_input_buffer()

    def lines(self, cmd, timeout):
        """Send cmd, yield (host time, line) until its result line; raises EOFError if the board goes away"""
        self.s.write(cmd.encode() + b"\n")
        end = time.time() + timeout
        while time.time() < end:
            try:
                raw = self.s.readline()
            except OSError as e:  # the port vanished: the board reset
                raise EOFError(str(e))
            if raw:
                line = raw.decode(errors="replace").strip()
                yield time.time(), line
                if line.startswith("R "):
                    return
        raise TimeoutError(cmd)

    def cmd(self, cmd, timeout=15):
        for _, line in self.lines(cmd, timeout):
            if line.startswith("R "):
                return json.loads(line[2:])


def host_scan(name, secs=8):
    """The board's advertising as the Mac hears it: best RSSI, or None"""
    import asyncio
    from bleak import BleakScanner
    best = []

    def cb(dev, adv):
        if adv.local_name == name:
            best.append(adv.rssi)

    async def scan():
        async with BleakScanner(cb):
            await asyncio.sleep(secs)
    asyncio.run(scan())
    return max(best) if best else None


def hfxo_ppm(points):
    """Least-squares slope of the board's 16 MHz count against host time, in ppm (positive: the crystal runs fast)"""
    t = [p[0] for p in points]
    x = [p[1] / 16e6 for p in points]
    mt, mx = statistics.fmean(t), statistics.fmean(x)
    slope = sum((a - mt) * (b - mx) for a, b in zip(t, x)) / sum((a - mt) ** 2 for a in t)
    resid = statistics.pstdev([b - mx - slope * (a - mt) for a, b in zip(t, x)])
    return (slope - 1) * 1e6, resid * 1e3


def mic_verdict(quiet, qb, loud, lb):
    """(quiet ok, floor, speech ok, rise) from the two mic runs (each: its result, each 100 ms block's AC RMS). Speech is
    judged only on a mic that passed the quiet run: a mic without power or with DAT stuck still gives a start-up
    transient that decays to a flat line (1410 -> 0 over 2 s), and its loudest block stood +32 dB above that floor"""
    floor = statistics.median(qb)
    quiet_ok = bool(quiet["ok"]) and floor < 300 and not (quiet["flats"] or quiet["full"])
    rise = 20 * math.log10(max(max(lb), 1) / max(floor, 1))
    return quiet_ok, floor, quiet_ok and bool(loud["ok"]) and rise >= 10, rise


def check():
    """mic_verdict on recorded runs: a dead mic (power shorted) and a good one (quiet room, the Mac's speech too far)"""
    decay = [1410, 954, 645, 436, 295, 199, 135, 91, 61, 41, 28, 19, 12, 8, 5, 3, 2, 1, 1, 0]
    dead = {"ok": 1, "flats": 17, "full": 0}
    assert mic_verdict(dead, decay, dead, decay + [0] * 10)[::2] == (False, False)
    good = {"ok": 1, "flats": 0, "full": 0}
    qb = [48, 55, 43, 30, 32, 29, 29, 37, 35, 45, 37, 30, 31, 35, 46, 32, 37, 36, 37, 50]
    lb = [61, 53, 50, 35, 40, 43, 43, 38, 34, 36, 61, 72, 39, 67, 68, 47, 99, 55, 69, 66]
    assert mic_verdict(good, qb, good, lb)[::2] == (True, False)  # +8.7 dB: LOOK
    assert mic_verdict(good, qb, good, lb + [400])[::2] == (True, True)  # someone speaks close: PASS
    print("mic_verdict: ok")


def dbfs(v):
    return f"{20 * math.log10(max(v, 1) / 32768):.0f} dBFS"


SD_HINTS = {  # where the card start-up stops, and what to look at
    "cmd0": "the card never answered", "cmd8": "the card garbled its echo (SCK/MOSI/MISO noise?)",
    "acmd41": "the card never left idle (supply too weak?)", "cmd58": "no OCR", "csd": "no CSD",
    "read": "a block read at 8 MHz failed", "write": "a block write at 8 MHz failed", "readback": "reading it back failed",
    "verify": "it read back different"}


def run(serial, clk_secs, json_out, p3=False, flip=20):
    b = Board(serial)
    rows, res = [], {}

    def row(item, ok, detail):
        rows.append((item, "PASS" if ok is True else "FAIL" if ok is False else ok, detail))

    info = res["info"] = b.cmd("info")
    row("chip", True, f"nRF{info['part']} {info['variant']} id {info['id']} RAM {info['ram_kb']} KB flash "
        f"{info['flash_kb']} KB; UICR REGOUT0 {info['regout0']} PSELRESET {info['pselreset']} APPROTECT "
        f"{info['approtect']} NFCPINS {info['nfcpins']}; reset reason {info['resetreas']}; BLE name suffix {info['ble']}")
    row("USB", True, f"enumerated as {PRODUCT} {serial}, serial port {b.s.port}")
    row("32 MHz crystal start", info["hfxo_ok"] and info["hfxo_us"] < 5000, f"{info['hfxo_us']} us")
    lf_ok = info["lfxo_ok"] and info["lfxo_ms"] < 1000
    row("32.768 kHz crystal start", lf_ok, f"{info['lfxo_ms']} ms" if info["lfxo_ok"] else "never started")
    row("32.768 kHz vs 32 MHz", "INFO", f"{info['lfxo_ppb_vs_hfxo'] / 1000:+.1f} ppm" if lf_ok else "no crystal")

    rssi = res["tx_rssi"] = host_scan(f"LRT-{serial}")
    row("radio TX (Mac hears it)", rssi is not None and rssi >= -80,
        f"RSSI {rssi} dBm (limit -80, board within 1 m)" if rssi is not None else "not heard in 8 s")
    rx = res["rx"] = b.cmd("rx")
    row("radio RX (board hears others)", bool(rx["ok"]), f"{rx['devices']} devices, best RSSI {rx['best_rssi']} dBm")

    g = res["gpio"] = b.cmd("gpio p3" if p3 else "gpio")  # p3: never drives a pin with a part on it
    row("GPIO pads", bool(g["ok"]), f"{g['pins']} pins; stuck {g['stuck'] or 'none'}; shorts {g['shorts'] or 'none'}"
        + ("" if g["nfc_as_gpio"] else "; P0.09/P0.10 skipped (NFC mode)")
        + ("; the 8 LR-P3 pins left out (checked by the SD, mic and switch rows)" if p3 else ""))
    a = res["adc"] = b.cmd("adc")
    row("VDD (regulator)", abs(a["vdd_mv"] - a["vdd_expected_mv"]) < a["vdd_expected_mv"] * 0.05,
        f"{a['vdd_mv']} mV, expected {a['vdd_expected_mv']} ±5%")
    row("VDDH (USB/battery input)", 4300 < a["vddh_mv"] < 5500,
        f"{a['vddh_mv']} mV on USB (4300-5500); P0.04 (nice!nano v2 battery divider) {a['p0_04_mv']} mV")
    row("charge status pin", "INFO", "none on this footprint (the charge LED is wired to the charger only)")

    if p3:
        sd = res["sd"] = b.cmd("sd", 60)
        reads = (f"{sd['reads']} blocks read at 8 MHz, {sd['read_errs']} failed ({sd['crc_errs']} CRC errors: bits "
                 f"lost on the wires) in {sd['read_ms']} ms; last sector write {sd['write_us'] / 1000:.1f} ms (busy "
                 f"{sd['busy_ms']} ms), restored {sd['restored']}")
        if not sd["fail"]:
            row("SD card (VCC P0.13, SPI)", bool(sd["ok"]), f"{sd['sectors'] * 512 / 1e9:.2f} GB, OCR {sd['ocr']}, "
                f"{'MBR, first partition type ' + sd['part'] if sd['mbr'] else 'no MBR'} (0x0B/0x0C FAT32, 0x07 "
                f"exFAT: our firmware needs one of them); start-up {sd['init_ms']} ms; {reads}")
        else:
            row("SD card (VCC P0.13, SPI)", False, f"failed at {sd['fail']}: {SD_HINTS.get(sd['fail'], '')}; MISO idle "
                f"{sd['idle']}, CMD0 {sd['cmd0']} (0x01 expected; 0xFF: nobody on MISO P1.00, 0x00: MISO held low), "
                f"CMD8 {sd['cmd8']}, ACMD41 {sd['acmd41']}, write response {sd['resp']}; {reads}. Check the module's "
                "3V3 (VCC) and GND, CS P0.20, MOSI P0.22, SCK P0.24, MISO P1.00, and that a card is in")
        def mic(secs):  # the mic's result, and each 100 ms block's AC RMS
            blocks = []
            for _, line in b.lines(f"mic {secs}", secs + 15):
                if line.startswith("A "):
                    blocks.append(int(line.split()[2]))
                elif line.startswith("R "):
                    return json.loads(line[2:]), blocks
        quiet, qb = res["mic_quiet"] = mic(2)
        say = subprocess.Popen(["say", "one two three four five six seven eight nine ten"])  # the Mac's speaker
        loud, lb = res["mic_speech"] = mic(3)
        say.wait()
        quiet_ok, floor, heard, rise = mic_verdict(quiet, qb, loud, lb)
        row("mic (PDM, 2 s quiet)", quiet_ok, f"noise floor {floor:.0f} "
            f"({dbfs(floor)}, median 100 ms RMS; pass under 300: a quiet room gives tens); DC {quiet['mean']}, min "
            f"{quiet['min']} max {quiet['max']}; dropouts: {quiet['flats']} runs of >=10 ms identical samples "
            f"({quiet['flat_ms']} ms), {quiet['full']} full-scale samples" + ("" if quiet["ok"] else ": flat, DAT stuck. Check the mic's 3V (P0.17), GND, CLK "
                                 "(P0.08), DAT (P0.06)") + ("" if floor < 300 else ": noise, not sound. Check SEL is on "
                                                            "3V (mic N r tells which clock half the mic drives), CLK"))
        row("mic hears speech", "PASS" if heard else "LOOK", ("" if quiet_ok else "not judged: the quiet row failed; ")
            + f"loudest 100 ms {dbfs(max(lb))} against the floor {dbfs(floor)}: {rise:+.1f} dB (pass: +10 dB; the Mac "
            "speaks numbers meanwhile; if it is far or muted, talk to the board and run again)")
        if flip:
            print(f"flip the switch both ways within {flip} s")
        seen, end = [], time.time() + flip
        while True:
            on = b.cmd("sw")["on"]
            seen += [on] if on not in seen else []
            if len(seen) == 2 or time.time() >= end:
                break
            time.sleep(0.2)
        res["switch"] = seen
        row("switch (P1.15)", "PASS" if len(seen) == 2 else "LOOK", "read ON (closed to GND) and OFF (open)"
            if len(seen) == 2 else f"reads {'ON (closed to GND)' if seen[0] else 'OFF (open)'}; not flipped within "
            f"{flip} s: run again with someone flipping it (an open wire also reads OFF)")

    b.cmd("led")
    row("blue LED (P0.15)", "LOOK", "blinked 5 times: check by eye")

    points = [(t, int(l.split()[2])) for t, l in b.lines(f"clk {clk_secs}", clk_secs + 15) if l.startswith("T ")]
    ppm, jitter = hfxo_ppm(points)
    res["hfxo_ppm"] = ppm
    row("32 MHz crystal accuracy", abs(ppm) <= 40,
        f"{ppm:+.1f} ppm against the Mac's clock over {clk_secs} s (limit ±40; fit residual {jitter:.2f} ms)")
    row("32.768 kHz accuracy", abs(info["lfxo_ppb_vs_hfxo"] / 1000 + ppm) <= 100 if lf_ok else False,
        f"{info['lfxo_ppb_vs_hfxo'] / 1000 + ppm:+.1f} ppm absolute (limit ±100: our ProMicro firmware widens its BLE "
        "receive windows for a sleep clock within 100 ppm, lr.sh K32SRC_100PPM)" if lf_ok else "no crystal")

    try:
        stage = None
        for _, line in b.lines("dcdc", 15):
            if line.startswith("S "):
                stage = line.split()[2]
            elif line.startswith("R "):
                d = res["dcdc"] = json.loads(line[2:])
                row("DCDC converters (inductors)", bool(d["ok"]), f"VDD LDO {d['vdd_ldo_mv']} / REG1 DCDC "
                    f"{d['vdd_reg1_mv']} / REG0 DCDC {d['vdd_reg0_mv']} mV")
    except (EOFError, TimeoutError):
        row("DCDC converters (inductors)", False, f"the board reset when {stage} DCDC was turned on: its inductor "
            "is missing or broken (our firmware must stay on the LDO)")

    width = max(len(r[0]) for r in rows)
    print(f"\nself-test {serial}{' LR-P3' if p3 else ''} {time.strftime('%Y-%m-%d %H:%M:%S')}")
    for item, verdict, detail in rows:
        print(f"{verdict:4}  {item:{width}}  {detail}")
    failed = [r[0] for r in rows if r[1] == "FAIL"]
    print(f"\n{'FAIL: ' + ', '.join(failed) if failed else 'PASS'} ({len(rows)} items; LOOK: by eye, INFO: not judged)")
    if json_out:
        json.dump({"serial": serial, "time": time.time(), "rows": rows, "raw": res}, open(json_out, "w"), indent=1)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["build", "flash", "run", "check"])
    ap.add_argument("serial", nargs="?", help="the board's USB serial number (16 hex digits)")
    ap.add_argument("--clk", type=int, default=60, help="seconds of 32 MHz crystal timing (default 60)")
    ap.add_argument("--json", help="also write the report here")
    ap.add_argument("--p3", action="store_true", help="the board is wired into an LR-P3: also test its SD card, mic and "
                    "switch, and never drive their pins")
    ap.add_argument("--flip", type=int, default=20, help="--p3: seconds to wait for the switch to be flipped (default 20)")
    a = ap.parse_args()
    if a.action == "check":
        sys.exit(check())
    if a.action != "build" and not re.fullmatch(r"[0-9A-F]{16}", a.serial or ""):
        ap.error("SERIAL: 16 hex digits, as ioreg shows it")
    build() if a.action == "build" else flash(a.serial) if a.action == "flash" else run(a.serial, a.clk, a.json, a.p3, a.flip)
