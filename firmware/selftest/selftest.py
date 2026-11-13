# /// script
# dependencies = ["pyserial", "bleak"]
# ///
"""Board self-test for nRF52840 Pro Micro boards, LR-N4 and LR-N5 (README.md). macOS host; boards are named by their USB
serial number (the chip's DEVICEID, the same in the UF2 bootloader and in this firmware).

  python3 selftest.py build [--n4|--n5] the firmware (NCS v2.7.0, as lr.sh); --n4 / --n5: LR-N4's / LR-N5's, in build-n4/5
  uv run selftest.py flash SERIAL [--n4|--n5]   UF2 onto that board only: its bootloader drive, or this firmware's dfu
  uv run selftest.py flash SERIAL --uf2 F   another UF2 the same way (the recording firmware after the self-test)
  uv run selftest.py run SERIAL [--clk S] [--json FILE]   every check, a PASS/FAIL report; exit 1 on any FAIL
  uv run selftest.py run SERIAL --p3 [--flip S]           a board wired into an LR-P3: also its SD card, mic and switch
  uv run selftest.py run SERIAL --n4 [--flip S] [--cover S]   an LR-N4: also SD, both mics, IMU, CHRG, switch
  uv run selftest.py run SERIAL --n5 [--flip S] [--cover S]   an LR-N5: the same with its NAND in place of the SD card
  ... run ... --no-dcdc                  without the DCDC test (a board without L1 / L3: VDD would collapse)
  python3 selftest.py swd                an LR-N4 on the Pico probe after its first flash: reads back FICR and UICR
  python3 selftest.py check              the mic verdicts and the SWD read-back parser on recorded output (no board)
"""
import argparse, json, math, os, plistlib, re, shutil, statistics, subprocess, sys, time

D = os.path.dirname(os.path.abspath(__file__))
PRODUCT = "LR selftest"


def out_dir(board):
    return f"{D}/build" if board == "promicro" else f"{D}/build-{board}"


def build(board):
    os.chdir(os.environ.get("NCS", "/opt/nordic/ncs/v2.7.0"))  # a west workspace of NCS v2.7.0
    tc = [] if os.environ.get("ZEPHYR_SDK_INSTALL_DIR") else ["nrfutil", "sdk-manager", "toolchain", "launch",
                                                             "--ncs-version", "v2.7.0", "--"]  # CI: loaded already
    subprocess.run(tc + ["west", "build", "-b", "xiao_ble/nrf52840", "-d", out_dir(board), D, "--",
                         "-DNCS_TOOLCHAIN_VERSION=NONE", f"-DDTC_OVERLAY_FILE={D}/promicro.overlay"]
                   + ({"n4": ["-DLR_N4=1"], "n5": ["-DLR_N5=1"]}.get(board, [])), check=True)
    print(f"built {out_dir(board)}/zephyr/zephyr.uf2 (UF2) and zephyr.hex (SWD, at 0x26000)")


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


def flash(serial, board, uf2=None):
    selftest, uf2 = not uf2, uf2 or f"{out_dir(board)}/zephyr/zephyr.uf2"
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
    print(f"{serial}: {open(f'{mp}/INFO_UF2.TXT').readline().strip()} at {mp}; copying {uf2}")
    try:
        shutil.copyfile(uf2, f"{mp}/selftest.uf2")
    except OSError:
        pass  # the board reboots as the last block lands
    for _ in range(60):
        time.sleep(0.5)
        if selftest and port(serial):
            return print(f"{serial}: {PRODUCT} is up on {port(serial)}")
        if not selftest and usb_devices(serial) and not drive(serial):  # another firmware, back on USB as itself
            return print(f"{serial}: copied; up as {usb_devices(serial)[0][0]}")
    sys.exit(f"{serial}: {PRODUCT if selftest else 'the board'} not back on USB under this serial within 30 s")


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


# LR-N4's first flash over SWD (the hardware repo's assembly notes): after the one pyocd load of bootloader + UICR + app,
# these read back with the CPU still halted. PSELRESET[0] / [1] = P0.18 connected, REGOUT0 VOUT = 4 (3.0 V), NRFFW[0] /
# [1] as the bootloader's HEX writes them; UICR APPROTECT 0x5A (HwDisabled) on build code F and later, erased before
SWD_READS = {"part": 0x10000100, "variant": 0x10000104, "pselreset0": 0x10001200, "pselreset1": 0x10001204,
             "approtect": 0x10001208, "regout0": 0x10001304, "nrffw0": 0x10001014, "nrffw1": 0x10001018,
             "approtect_disable": 0x40000558, "deviceid0": 0x10000060, "deviceid1": 0x10000064}
PYOCD = ["pyocd", "commander", "-t", "nrf52840", "-O", "resume_on_disconnect=false", "-O", "auto_unlock=false",
         "--connect", "halt"]


def swd_verdict(out):
    """Rows (item, verdict, detail) from pyocd commander's output for the SWD_READS, readap 1 0x0c and status"""
    words = {int(a, 16): int(v, 16) for a, v in re.findall(r"^([0-9a-f]{8}):\s+([0-9a-f]{8})\b", out, re.M)}
    r = {k: words.get(a) for k, a in SWD_READS.items()}
    ap = re.search(r"AP register 0x100000c = 0x([0-9a-f]+)", out)
    if r["part"] is None or not ap:
        return [("SWD", "FAIL", "no read-back (locked, or not connected): " + out.strip()[-300:])]
    variant = r["variant"].to_bytes(4, "big").decode(errors="replace")
    fxx = variant[2] >= "F"
    serial = f"{r['deviceid1']:08X}{r['deviceid0']:08X}" if None not in (r["deviceid0"], r["deviceid1"]) else "?"
    ok = lambda b: "PASS" if b else "FAIL"
    return [
        ("chip (FICR)", ok(r["part"] == 0x52840), f"INFO.PART 0x{r['part']:X}, VARIANT {variant} (build code "
         f"{variant[2:]}: {'Fxx or later, ships locked' if fxx else 'before Fxx'}); USB serial (DEVICEID) {serial}"),
        ("CTRL-AP APPROTECTSTATUS", ok(int(ap.group(1), 16) == 1), f"0x{int(ap.group(1), 16):X} (1: open)"),
        ("UICR PSELRESET[0] / [1]", ok(r["pselreset0"] == r["pselreset1"] == 0x12),
         f"0x{r['pselreset0']:08X} / 0x{r['pselreset1']:08X} (0x00000012: P0.18 is RESET)"),
        ("UICR REGOUT0", ok(r["regout0"] & 7 == 4), f"0x{r['regout0']:08X} (VOUT 4: VDD 3.0 V; 7 is the erased 1.8 V)"),
        ("UICR NRFFW[0] / [1]", ok((r["nrffw0"], r["nrffw1"]) == (0xF4000, 0xFE000)),
         f"0x{r['nrffw0']:X} / 0x{r['nrffw1']:X} (the bootloader at 0xF4000, its settings 0xFE000)"),
        ("UICR APPROTECT", ok(r["approtect"] == (0x5A if fxx else 0xFFFFFFFF)), f"0x{r['approtect']:08X} "
         f"({'0x0000005A: SWD stays open once the app writes DISABLE' if fxx else '0xFFFFFFFF on a pre-Fxx chip'})"),
        ("APPROTECT.DISABLE", "INFO", f"0x{r['approtect_disable']:X} (0x5A once an app has booted on an Fxx chip; "
         "not yet right after the first flash)" if r["approtect_disable"] is not None else "not read"),
        ("CPU", ok(re.search(r"^Core \d+ \(.*\):\s+Halted", out, re.M) is not None), "halted: the app has not run"),
    ]


def swd():
    cmds = ["readap 1 0x0c"] + [f"read32 0x{a:08x}" for a in SWD_READS.values()] + ["status"]
    out = subprocess.run(PYOCD + ["-c", *cmds], capture_output=True, text=True)
    rows = swd_verdict(out.stdout + out.stderr)
    for item, verdict, detail in rows:
        print(f"{verdict:4}  {item:24}  {detail}")
    return 1 if any(r[1] == "FAIL" for r in rows) else 0


def check():
    """mic_verdict on recorded runs: a dead mic (power shorted) and a good one (quiet room, the Mac's speech too far);
    swd_verdict on output in pyocd 0.45.1's formats (dump_hex_data, readap)"""
    decay = [1410, 954, 645, 436, 295, 199, 135, 91, 61, 41, 28, 19, 12, 8, 5, 3, 2, 1, 1, 0]
    dead = {"ok": 1, "flats": 17, "full": 0}
    assert mic_verdict(dead, decay, dead, decay + [0] * 10)[::2] == (False, False)
    good = {"ok": 1, "flats": 0, "full": 0}
    qb = [48, 55, 43, 30, 32, 29, 29, 37, 35, 45, 37, 30, 31, 35, 46, 32, 37, 36, 37, 50]
    lb = [61, 53, 50, 35, 40, 43, 43, 38, 34, 36, 61, 72, 39, 67, 68, 47, 99, 55, 69, 66]
    assert mic_verdict(good, qb, good, lb)[::2] == (True, False)  # +8.7 dB: LOOK
    assert mic_verdict(good, qb, good, lb + [400])[::2] == (True, True)  # someone speaks close: PASS
    print("mic_verdict: ok")
    words = {0x10000100: 0x52840, 0x10000104: 0x41414630, 0x10001200: 0x12, 0x10001204: 0x12, 0x10001208: 0x5A,
             0x10001304: 0xFFFFFFFC, 0x10001014: 0xF4000, 0x10001018: 0xFE000, 0x40000558: 0,
             0x10000060: 0x89ABCDEF, 0x10000064: 0x01234567}
    out = "AP register 0x100000c = 0x00000001\n" + "".join(f"{a:08x}:  {v:08x}    |....|\n" for a, v in words.items())
    good = swd_verdict(out + "Core 0 (cortex_m):  Halted\n")
    assert [r[1] for r in good] == ["PASS"] * 6 + ["INFO", "PASS"], good
    assert good[0][2].endswith("DEVICEID) 0123456789ABCDEF"), good[0]
    bad = swd_verdict(out.replace("fffffffc", "ffffffff").replace("0000005a", "ffffffff") + "Core 0 (cortex_m):  Running\n")
    assert [r[0] for r in bad if r[1] == "FAIL"] == ["UICR REGOUT0", "UICR APPROTECT", "CPU"], bad
    assert swd_verdict("Error: target is locked")[0][1] == "FAIL"
    print("swd_verdict: ok")


def dbfs(v):
    return f"{20 * math.log10(max(v, 1) / 32768):.0f} dBFS"


SD_HINTS = {  # where the card start-up stops, and what to look at
    "cmd0": "the card never answered", "cmd8": "the card garbled its echo (SCK/MOSI/MISO noise?)",
    "acmd41": "the card never left idle (supply too weak?)", "cmd58": "no OCR", "csd": "no CSD",
    "read": "a block read at 8 MHz failed", "write": "a block write at 8 MHz failed", "readback": "reading it back failed",
    "verify": "it read back different"}


NAND_HINTS = {  # where the NAND test stops, and what to look at
    "busy": "the chip stayed busy after its reset (or MISO P1.00 is held high)",
    "id": "no W25N JEDEC ID (EF AA 21 / EF AA 22): 00 00 00 is nobody on MISO P1.00; check MEM_VDD (U3, MEM_EN P0.29), "
          "/CS P0.20, CLK P0.24, DI P0.22, DO P1.00 and U4's joints",
    "status": "the status registers did not take the writes (protection bits, ECC-E / BUF): DI P0.22 or /WP (R28)",
    "bad": "16 blocks at the end all marked bad", "no_blank": "no blank block among the last 16: the recorder's data "
    "is on them (nand w would erase one)", "erase": "a block erase failed or timed out", "program": "a page program "
    "failed or timed out", "verify": "a page read back different", "blank": "the block did not read blank after its erase"}


def run(serial, clk_secs, json_out, p3=False, flip=20, board="promicro", cover=5, dcdc=True):
    b = Board(serial)
    rows, res = [], {}
    n4, n5 = board in ("n4", "n5"), board == "n5"  # LR-N5 is LR-N4 with a NAND in place of the SD card
    name = {"n4": "LR-N4", "n5": "LR-N5"}.get(board, "ProMicro")

    def row(item, ok, detail):
        rows.append((item, "PASS" if ok is True else "FAIL" if ok is False else ok, detail))

    info = res["info"] = b.cmd("info")
    if info.get("board", "promicro") != board:  # the other build's pins: its gpio would drive this board's parts
        sys.exit(f"{serial} runs the {info.get('board', 'promicro')} self-test; flash the {name} one first "
                 f"(selftest.py flash {serial}{'' if board == 'promicro' else ' --' + board})")
    chip = (f"nRF{info['part']} {info['variant']} id {info['id']} RAM {info['ram_kb']} KB flash "
            f"{info['flash_kb']} KB; UICR REGOUT0 {info['regout0']} PSELRESET {info['pselreset']} APPROTECT "
            f"{info['approtect']} NFCPINS {info['nfcpins']}; reset reason {info['resetreas']}; BLE name suffix {info['ble']}")
    nfc_ok = not n5 or int(info["nfcpins"], 16) & 1 == 0  # LR-N5's UICR makes P0.09/P0.10 GPIOs (swd.py)
    if n4:  # what the first flash wrote (the UICR) and the app's unlock at boot on build code F and later
        fxx = info["variant"][2] >= "F"
        approtect = int(info["approtect"], 16) == (0x5A if fxx else 0xFFFFFFFF)
        unlocked = not fxx or int(info["approtect_disable"], 16) & 0xFF == 0x5A
        row("chip, UICR", int(info["regout0"], 16) & 7 == 4 and int(info["pselreset"], 16) == 0x12 and approtect
            and unlocked and nfc_ok, f"{chip}; APPROTECT.DISABLE {info['approtect_disable']} (pass: REGOUT0 VOUT 4 = "
            "3.0 V, PSELRESET 0x12, " + ("NFCPINS bit 0 clear (P0.09/P0.10 GPIOs), " if n5 else "")
            + ("Fxx: UICR APPROTECT 0x5A and DISABLE 0x5A, SWD open)" if fxx else "pre-Fxx: UICR APPROTECT erased)"))
        if int(info["regout0"], 16) & 7 != 4:  # VDD is then 3.3 V (the bootloader writes it into an erased REGOUT0)
            sys.exit(f"{serial}: UICR REGOUT0 {info['regout0']} is not VOUT 4 (3.0 V), so VDD is likely the bootloader's "
                     "3.3 V, past what the 3.0 V card, mics and IMU take on their pins: nothing powered (the firmware "
                     "refuses too). Rewrite the UICR with the bootloader over SWD first")
    else:
        row("chip", True, chip)
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
    if n4:
        lines = "the 3 pulled-up lines (NAND /CS, IMU SDA SCL)" if n5 else "the 5 pulled-up lines (SD CS MOSI MISO, IMU SDA SCL)"
        row("GPIO balls", bool(g["ok"]), f"{g['pins']} free balls: stuck {g['stuck'] or 'none'}, shorts "
            f"{g['shorts'] or 'none'}; with the {'NAND' if n5 else 'SD'}, mic and IMU supplies on, {lines} stuck "
            f"{g['lines_stuck'] or 'none'}, shorts {g['lines_shorts'] or 'none'}"
            + (f"; low at rest, not watched: {g['watched_low']}" if g["watched_low"] else "")
            + ("" if g["nfc_as_gpio"] else "; P0.09/P0.10 skipped (NFC mode)"))
    else:
        row("GPIO pads", bool(g["ok"]), f"{g['pins']} pins; stuck {g['stuck'] or 'none'}; shorts {g['shorts'] or 'none'}"
            + ("" if g["nfc_as_gpio"] else "; P0.09/P0.10 skipped (NFC mode)")
            + ("; the 8 LR-P3 pins left out (checked by the SD, mic and switch rows)" if p3 else ""))
    a = res["adc"] = b.cmd("adc")
    lo = 0.90 if n4 else 0.95  # LR-N4: 2.70-3.15 V for its 3.0 V (first-board T-02)
    row("VDD (regulator)", a["vdd_expected_mv"] * lo <= a["vdd_mv"] <= a["vdd_expected_mv"] * 1.05,
        f"{a['vdd_mv']} mV, expected {a['vdd_expected_mv']} {'-10/+5' if n4 else '±5'}%")
    if n4:
        row("VDDH (system rail)", 4300 < a["vddh_mv"] < 5500, f"{a['vddh_mv']} mV on USB (4300-5500: USB through D2)")
        row("battery (AIN0, 1M/1M)", "INFO", f"{a['vbat_mv']} mV (no cell: the charger's output, or about 0)")
        c = res["chrg"] = b.cmd("chrg")
        state = {0: "charging (low)", 1: "done or no cell (high)"}.get(c["level"], "not read: no VBUS")
        # a cell well below 4.2 V on USB must be charging; full, or no cell (the charger's output hunts): not judged
        judge = 2500 < c["vbat_mv"] < 4100
        row(f"charge status (CHRG {'P0.31' if n5 else 'P0.29'})", (c["level"] == 0) if judge else "INFO", f"{state}, battery {c['vbat_mv']} "
            "mV" + (" (under 4.1 V on USB: must read charging)" if judge else " (judged only with a cell under 4.1 V)"))
    else:
        row("VDDH (USB/battery input)", 4300 < a["vddh_mv"] < 5500,
            f"{a['vddh_mv']} mV on USB (4300-5500); P0.04 (nice!nano v2 battery divider) {a['p0_04_mv']} mV")
        row("charge status pin", "INFO", "none on this footprint (the charge LED is wired to the charger only)")

    if n5:
        nd = res["nand"] = b.cmd("nand", 30)
        row("NAND (U4, MEM_EN P0.29, SPI)", bool(nd["ok"]), f"JEDEC ID {nd['id']} ({nd['blocks']} blocks), SR-1 {nd['sr1']} "
            f"SR-2 {nd['sr2']}; block {nd['block']} erased, first and last pages programmed and read back (ECC status "
            f"{nd['ecc']}), erased again: erase {nd['erase_us'] / 1000:.1f} ms, program {nd['program_us'] / 1000:.2f} ms; "
            f"{nd['bad_seen']} bad blocks at the end" + (f"; failed at {nd['fail']}: {NAND_HINTS.get(nd['fail'], '')}"
                                                         if nd["fail"] else ""))
        # no SAADC input reaches either LDO's output: MEM_VDD by the /CS line its R26 pulls up, MIC_VDD by the mics' data
        row("MEM_VDD (U3 on / off)", nd["cs_on"] == 1 and ("PASS" if nd["cs_off"] == 0 else "LOOK"),
            f"/CS P0.20 read {nd['cs_on']} with MEM_EN up (R26 to MEM_VDD: 1 = on), {nd['cs_off']} 300 ms after it went "
            "down (0: off; 1: MEM_VDD still up, measure TP4 with `vcc 30`)")
    if p3 or (n4 and not n5):
        sd_label = "SD card (SD_EN P0.31, SPI)" if n4 else "SD card (VCC P0.13, SPI)"
        sd = res["sd"] = b.cmd("sd", 60)
        reads = (f"{sd['reads']} blocks read at 8 MHz, {sd['read_errs']} failed ({sd['crc_errs']} CRC errors: bits "
                 f"lost on the wires) in {sd['read_ms']} ms; last sector write {sd['write_us'] / 1000:.1f} ms (busy "
                 f"{sd['busy_ms']} ms), restored {sd['restored']}")
        if not sd["fail"]:
            row(sd_label, bool(sd["ok"]), f"{sd['sectors'] * 512 / 1e9:.2f} GB, OCR {sd['ocr']}, "
                f"{'MBR, first partition type ' + sd['part'] if sd['mbr'] else 'no MBR'} (0x0B/0x0C FAT32, 0x07 "
                f"exFAT: our firmware needs one of them); start-up {sd['init_ms']} ms; {reads}")
        else:
            row(sd_label, False, f"failed at {sd['fail']}: {SD_HINTS.get(sd['fail'], '')}; MISO idle "
                f"{sd['idle']}, CMD0 {sd['cmd0']} (0x01 expected; 0xFF: nobody on MISO P1.00, 0x00: MISO held low), "
                f"CMD8 {sd['cmd8']}, ACMD41 {sd['acmd41']}, write response {sd['resp']}; {reads}. Check "
                + ("SD_VDD (U3's 3.0 V, enabled by P0.31)" if n4 else "the module's 3V3 (VCC) and GND")
                + ", CS P0.20, MOSI P0.22, SCK P0.24, MISO P1.00, and that a card is in")
    if p3 or n4:
        def mic(secs, stereo=False):  # the mic's result, and each block's AC RMS (stereo: a list per channel)
            blocks = [[], []]
            for _, line in b.lines(f"mic {secs}{' s' if stereo else ''}", secs + 15):
                if line.startswith("A "):
                    f = line.split()
                    blocks[0].append(int(f[2]))
                    if stereo:
                        blocks[1].append(int(f[5]))
                elif line.startswith("R "):
                    return json.loads(line[2:]), blocks if stereo else blocks[0]

        def speech(secs=3):  # a run while the Mac's speaker says numbers
            say = subprocess.Popen(["say", "one two three four five six seven eight nine ten"])
            r = mic(secs, n4)
            say.wait()
            return r
        quiet, qb = res["mic_quiet"] = mic(2, n4)
        loud, lb = res["mic_speech"] = speech()
        if n4:
            n4_mics(row, res, quiet, qb, loud, lb, speech, cover)
        if n5:  # no SAADC input on MIC_VDD either: both mics giving data is U5's output on
            row("MIC_VDD (U5 on)", bool(quiet["ok"]), "both mics gave data with MIC_PWR P0.12 up (measure TP5 with `vcc 30` "
                "for the voltage)")
    if p3:
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
    if n4:
        m = res["imu"] = b.cmd("imu")
        row("IMU (LSM6DS3TR-C, I2C P0.23/P0.19 100 kHz, INT1 P0.11)", bool(m["ok"]), f"WHO_AM_I {m['who_am_i']} (0x6A), "
            f"INT1 data-ready {m['drdy']}/10, reads {m['reads']}/10; accel {m['accel_mg']} mg, |a| {m['accel_abs_mg']} "
            f"mg (800-1200, lying still), gyro max {m['gyro_dps_max']} dps (under 25)"
            + ("" if m["i2c"] else ": no answer on I2C. Check IMU_VDD (P1.08), R40/R41, U6's joints"))
    if p3 or n4:
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
    if n4:
        row("LEDs (D1: P0.15 yellow-green, P1.13 red)", "LOOK", "each blinked 5 times in turn: check both by eye")
    else:
        row("blue LED (P0.15)", "LOOK", "blinked 5 times: check by eye")

    points = [(t, int(l.split()[2])) for t, l in b.lines(f"clk {clk_secs}", clk_secs + 15) if l.startswith("T ")]
    ppm, jitter = hfxo_ppm(points)
    res["hfxo_ppm"] = ppm
    row("32 MHz crystal accuracy", abs(ppm) <= 40,
        f"{ppm:+.1f} ppm against the Mac's clock over {clk_secs} s (limit ±40; fit residual {jitter:.2f} ms)")
    lf_ppm = info["lfxo_ppb_vs_hfxo"] / 1000 + ppm
    # Two different numbers. 250 ppm is what the recording firmware declares to the BLE stack on every board, ProMicro
    # and LR-N4 alike (lr.sh K32SRC_250PPM): the sleep clock's error at any temperature, which sets how wide each
    # connection's receive window opens. 50 is LR-N4's room-temperature screen here, not a BLE limit: its Seiko SC-16S
    # is ±20 ppm at 25 °C (±10 more after reflow) and drifts to -194 at -40 °C, so it must read within 50 now for the
    # cold end to stay inside the 250 declared. The ProMicro clones' crystals have no datasheet: judged on the 250 alone
    lf_limit = 50 if n4 else 250
    row("32.768 kHz accuracy", abs(lf_ppm) <= lf_limit if lf_ok else False,
        f"{lf_ppm:+.1f} ppm absolute (limit ±{lf_limit}: " + ("room temperature, so the crystal's cold-end drift still "
        "fits the 250 ppm the firmware declares" if n4 else "our ProMicro firmware widens its BLE receive windows for "
        "a sleep clock within 250 ppm, lr.sh K32SRC_250PPM") + ")" + ("" if abs(lf_ppm) <= lf_limit else ": past it "
        "the board drops phone links, replace the 32.768 kHz crystal" if not n4 else ": check X1 and its two load "
        "capacitors (its CL is 7 pF), or replace X1") if lf_ok else "no crystal")

    if not dcdc:  # a board without L1 (the first N5): REG0 on its DCDC would pull VDD down (lab 11-11)
        row("DCDC converters (inductors)", "LOOK", "not run (--no-dcdc)")
    try:
        stage = None
        for _, line in b.lines("dcdc", 15) if dcdc else ():
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
    print(f"\nself-test {serial}{' LR-P3' if p3 else ' ' + name if n4 else ''} {time.strftime('%Y-%m-%d %H:%M:%S')}")
    for item, verdict, detail in rows:
        print(f"{verdict:4}  {item:{width}}  {detail}")
    failed = [r[0] for r in rows if r[1] == "FAIL"]
    print(f"\n{'FAIL: ' + ', '.join(failed) if failed else 'PASS'} ({len(rows)} items; LOOK: by eye, INFO: not judged)")
    if json_out:
        json.dump({"serial": serial, "time": time.time(), "rows": rows, "raw": res}, open(json_out, "w"), indent=1)
    sys.exit(1 if failed else 0)


def n4_mics(row, res, quiet, qb, loud, lb, speech, cover):
    """LR-N4's two mics (stereo: MK1 left, MK2 right): each one's quiet floor and speech, then each hole covered in turn
    while the Mac speaks: the covered one's level against the other must fall 6 dB or more from the open run"""
    def ratio(blocks):  # MK1 against MK2 over a run, by energy
        return 10 * math.log10(max(sum(x * x for x in blocks[0]), 1) / max(sum(x * x for x in blocks[1]), 1))
    for k, name in enumerate(["MK1", "MK2"]):
        side = "lr"[k]
        quiet_ok, floor, heard, rise = mic_verdict(quiet[side], qb[k], loud[side], lb[k])
        q = quiet[side]
        row(f"{name} (PDM {'falling' if k == 0 else 'rising'} edge, 2 s quiet)", quiet_ok, f"noise floor {floor:.0f} "
            f"({dbfs(floor)}; pass under 300); DC {q['mean']}, min {q['min']} max {q['max']}; {q['flats']} runs of "
            f">=10 ms identical samples, {q['full']} full-scale" + ("" if q["ok"] else ": flat, DAT stuck or no mic on "
            "this half. Check MIC_VDD (U5, P0.12), CLK P0.08 through R35, DAT P0.06, the mic's joints"))
        row(f"{name} hears speech", "PASS" if heard else "LOOK", ("" if quiet_ok else "not judged: its quiet row "
            "failed; ") + f"loudest {dbfs(max(lb[k]))} against {dbfs(floor)}: {rise:+.1f} dB (pass: +10 dB)")
    base = ratio(lb)
    res["mic_cover"] = {}
    for k, name in enumerate(["MK1", "MK2"]):
        if not cover:
            row(f"{name} hole covered", "LOOK", "not run (--cover 0)")
            continue
        print(f"cover {name}'s sound hole with a fingertip, and keep it there: recording in {cover} s")
        time.sleep(cover)
        _, cb = res["mic_cover"][name] = speech()
        drop = (base - ratio(cb)) * (1 if k == 0 else -1)
        row(f"{name} hole covered", drop >= 6, f"{name} against the other fell {drop:+.1f} dB (pass: 6 dB or more; "
            "less: cover it fully and run again; a hole that still hears is a leak around the mic's ring or a blocked "
            "port on the other)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["build", "flash", "run", "check", "swd"])
    ap.add_argument("serial", nargs="?", help="the board's USB serial number (16 hex digits)")
    ap.add_argument("--clk", type=int, default=60, help="seconds of 32 MHz crystal timing (default 60)")
    ap.add_argument("--json", help="also write the report here")
    ap.add_argument("--p3", action="store_true", help="the board is wired into an LR-P3: also test its SD card, mic and "
                    "switch, and never drive their pins")
    ap.add_argument("--uf2", help="flash: this UF2 instead of the self-test (e.g. firmware/devkit/build/lr-n4/zephyr/"
                    "zephyr.uf2)")
    ap.add_argument("--n4", action="store_true", help="LR-N4 (build, flash, run): its own build, pins and checks: SD, "
                    "both mics, IMU, CHRG, switch, both LEDs")
    ap.add_argument("--no-dcdc", action="store_true", help="run: skip the DCDC test (a board without its L1 or L3)")
    ap.add_argument("--n5", action="store_true", help="LR-N5 (build, flash, run): LR-N4's checks with its NAND in place "
                    "of the SD card")
    ap.add_argument("--flip", type=int, default=20, help="--p3, --n4, --n5: seconds to wait for the switch to be flipped "
                    "(default 20)")
    ap.add_argument("--cover", type=int, default=5, help="--n4, --n5: seconds to cover each mic's hole before its run (default "
                    "5; 0: skip)")
    a = ap.parse_args()
    if a.action == "check":
        sys.exit(check())
    if a.action == "swd":
        sys.exit(swd())
    if a.p3 + a.n4 + a.n5 > 1:
        ap.error("one of --p3, --n4, --n5")
    board = "n5" if a.n5 else "n4" if a.n4 else "promicro"
    if a.action != "build" and not re.fullmatch(r"[0-9A-F]{16}", a.serial or ""):
        ap.error("SERIAL: 16 hex digits, as ioreg shows it")
    if a.action == "build":
        build(board)
    elif a.action == "flash":
        flash(a.serial, board, a.uf2)
    else:
        run(a.serial, a.clk, a.json, a.p3, a.flip, board, a.cover, not a.no_dcdc)
