# /// script
# requires-python = ">=3.10"
# dependencies = ["pyocd==0.45.1", "pyserial"]
# ///
"""LR-N5 over SWD with a Pico debug probe (README.md, "LR-N5"): the first flash and a quick check of a freshly reflowed
board. pyOCD is pinned (0.45.1) and run from this script's own environment; the probe is named by its unique ID (-u),
and a locked chip is never unlocked without --unlock.

  uv run swd.py check -u PROBE                           no halt, no reset, no flash / UICR / peripheral write: probe,
                                                         chip, UICR, lock state, supply; what to do next
  uv run swd.py flash -u PROBE --bootloader HEX [--unlock] [--out DIR]
                                                         bootloader + N5's UICR in one load, then every byte read back
  uv run swd.py diag -u PROBE [--usb yes|no] [--adc PORT|auto] [--out DIR]
                                                         reset reason, regulators, VBUS, VDD / VDDH / battery by the
                                                         chip's SAADC, GPIO balls under pulls (and the probe's own ADC)
  uv run swd.py uicr [--fxx] -o FILE                     N5's UICR as Intel HEX
  uv run swd.py test                                     offline checks of the parsing and the verdicts

The board is powered by the Pico's 3V3 on VDD, battery and the board's USB off (the hardware's first-flash procedure,
M11 = A); "diag" may have the board's USB plugged in only before its B side (D2) is fitted, to put 5 V on the VBUS ball.
"flash" refuses to load while the chip sees VDDH or VBUS.

Every connect, "check"'s attach too, writes the core's debug registers (pyOCD sets DHCSR.C_DEBUGEN, DEMCR.TRCENA, DWT
and FPB) and, with resume_on_disconnect=false, leaves them set: the chip stays in debug mode, drawing more current,
until it is powered off.
"""
import argparse, datetime, hashlib, json, os, re, subprocess, sys, time

# The probe and target options of every pyocd call: no resume at exit, never an implicit unlock (mass erase), sector
# erase only, and no pyocd.yaml from the working directory to change any of that
TARGET = ["-t", "nrf52840", "--no-config", "-O", "resume_on_disconnect=false", "-O", "chip_erase=sector"]
NO_UNLOCK = ["-O", "auto_unlock=false"]
PYOCD = [sys.executable, "-m", "pyocd"]

# nice!nano bootloader 0.10.0 on S140 6.1.1, the one LR-N4 and LR-N5 use (the hardware's assembly notes, 12.2)
BOOTLOADER_SHA256 = "9b6bd9fd499084b99c00cc8a0a73400a94b75085a23c0bc1f36045a1280d0b8e"

# LR-N5's UICR, nRF52840 PS v1.11 (UICR base 0x10001000, register table p.47): PSELRESET[0] / [1] = P0.18, port 0,
# connected (both must say so, p.89); NFCPINS PROTECT = 0: P0.09 / P0.10 are GPIOs (no NFC antenna on N5, the balls are
# unconnected, and as GPIOs the self-test's gpio checks them); REGOUT0 VOUT = 4: VDD 3.0 V, the 3.0 V NAND, mics and
# IMU (only the low 3 bits written to 0; the bootloader writes 3.3 V into an erased one). On build code F and later
# APPROTECT = 0x5A (HwDisabled), so SWD stays open once the app writes APPROTECT.DISABLE at boot; erased before F.
UICR = {0x10001200: 0x00000012, 0x10001204: 0x00000012, 0x1000120C: 0xFFFFFFFE, 0x10001304: 0xFFFFFFFC}
UICR_FXX = {0x10001208: 0x0000005A}
NRFFW = {0x10001014: 0x000F4000, 0x10001018: 0x000FE000}  # the bootloader's own HEX writes these (its start, settings)

FICR = {"part": 0x10000100, "variant": 0x10000104, "deviceid0": 0x10000060, "deviceid1": 0x10000064}


def uicr_words(fxx):
    return {**UICR, **(UICR_FXX if fxx else {})}


def intel_hex(words):
    """Intel HEX for 32-bit little-endian words: one extended linear address record per 64 KB, consecutive words in
    one data record (at most 16 bytes), as the hardware repo's hand-checked LR-N4 UICR files are laid out"""
    def rec(addr, typ, data):
        b = bytes([len(data), addr >> 8 & 0xFF, addr & 0xFF, typ]) + data
        return f":{b.hex().upper()}{(-sum(b)) & 0xFF:02X}\n"
    out, upper, run = "", None, []
    for a in sorted(words) + [None]:
        if run and (a is None or a != run[-1] + 4 or a >> 16 != run[0] >> 16 or len(run) == 4):
            data = b"".join(words[x].to_bytes(4, "little") for x in run)
            out += rec(run[0] & 0xFFFF, 0, data)
            run = []
        if a is None:
            break
        if a >> 16 != upper:
            upper = a >> 16
            out += rec(0, 4, upper.to_bytes(2, "big"))
        run.append(a)
    return out + ":00000001FF\n"


def hex_segments(path):
    """{start: bytes} of an Intel HEX file's contiguous runs (pyOCD's own intelhex)"""
    from intelhex import IntelHex
    ih = IntelHex(path)
    return {a: bytes(ih.tobinarray(start=a, end=b - 1)) for a, b in ih.segments()}


def pyocd(args, check=False):
    """A pyocd command; its output (stdout and stderr together)"""
    r = subprocess.run(PYOCD + args, capture_output=True, text=True)
    out = r.stdout + r.stderr
    if check and r.returncode:
        sys.exit(f"pyocd {' '.join(args[:1])} failed ({r.returncode}):\n{out[-2000:]}")
    return out


def commander(uid, connect, cmds):
    return pyocd(["commander", "-u", uid, *TARGET, *NO_UNLOCK, "--connect", connect, "-c", *cmds])


def reads(out):
    """read32 results in order: [(address, value)] (pyOCD 0.45.1 prints "aaaaaaaa:  vvvvvvvv    |....|")"""
    return [(int(a, 16), int(v, 16)) for a, v in re.findall(r"^([0-9a-f]{8}):\s+([0-9a-f]{8})\b", out, re.M)]


def ap_status(out):
    m = re.search(r"AP register 0x100000c = 0x([0-9a-f]+)", out)
    return int(m.group(1), 16) if m else None


def locked(out):
    return re.search(r"locked|APPROTECT", out, re.I) is not None and ap_status(out) != 1


def table(rows):
    w = max(len(r[0]) for r in rows)
    for item, verdict, detail in rows:
        print(f"{verdict:4}  {item:{w}}  {detail}")
    return [r[0] for r in rows if r[1] == "FAIL"]


def probes():
    out = pyocd(["json", "--probes"])
    try:
        return [b["unique_id"] for b in json.loads(out[out.index("{"):])["boards"]]
    except (ValueError, KeyError):
        sys.exit(f"pyocd json --probes: {out[-500:]}")


def need_probe(uid):
    import pyocd as p
    if p.__version__ != "0.45.1":
        sys.exit(f"pyOCD {p.__version__}: this procedure is checked with 0.45.1 only (run this script with uv run)")
    found = probes()
    if uid not in found:
        sys.exit(f"probe {uid} not connected (connected: {', '.join(found) or 'none'}); plug the Pico in, check -u")


# ---- check: read only -------------------------------------------------------------------------------------------

STATE = {**FICR, "pselreset0": 0x10001200, "pselreset1": 0x10001204, "approtect": 0x10001208, "nfcpins": 0x1000120C,
         "regout0": 0x10001304, "nrffw0": 0x10001014, "nrffw1": 0x10001018, "usbregstatus": 0x40000438,
         "mainregstatus": 0x40000640}
# A chip swd.py flash has written, power-cycled since: it sits in the bootloader, which does not open SWD, so build code F
# reads as locked again. That is expected and needs no mass erase
FLASHED_LOCKED = ("a board swd.py flash has already done and that was powered off since reads locked too (build code "
                  "F, sitting in the bootloader): that is expected, do NOT use --unlock; its first app goes over UF2")


def state(out):
    """The STATE words by name from a commander run that read them in STATE's order (None: not read)"""
    got = dict(reads(out))
    return {k: got.get(a) for k, a in STATE.items()}


def build_code(s):
    v = s["variant"].to_bytes(4, "big").decode(errors="replace")
    return v, v[2] >= "F"


def supply_problem(s):
    """M11 = A: only the Pico's 3V3 on VDD. What else the chip sees (battery or USB), or None"""
    on = [what for k, what in (("mainregstatus", "VDDH (MAINREGSTATUS 1: battery, or USB through D2; REG0 drives VDD "
                                                 "against the Pico's 3V3)"),
                               ("usbregstatus", "VBUS (USBREGSTATUS.VBUSDETECT 1: the board's USB)"))
          if s[k] is None or s[k] & 1]
    return ", ".join(on) or None


def check_rows(out):
    """(rows, next step) from an attach-mode read of the chip"""
    s, ap = state(out), ap_status(out)
    if s["part"] is None:
        if locked(out):
            return [("chip", "FAIL", "access port protected (APPROTECT): a new build code F chip ships locked")], \
                "a NEW chip only: swd.py flash --unlock (mass erase of flash and UICR; never on a board with data). " \
                + FLASHED_LOCKED
        return [("chip", "FAIL", "no answer over SWD: " + out.strip()[-300:])], \
            "check the wires by their silkscreen names (GND, DIO, CLK, RST, 3V3 on VDD) and that VDD is up"
    variant, fxx = build_code(s)
    erased = all(s[k] == 0xFFFFFFFF for k in ("pselreset0", "pselreset1", "approtect", "nfcpins", "regout0", "nrffw0"))
    rows = [("chip (FICR)", "PASS" if s["part"] == 0x52840 else "FAIL", f"INFO.PART 0x{s['part']:X}, VARIANT {variant} "
             f"(build code {variant[2:]}: {'F or later, its UICR gets APPROTECT 0x5A' if fxx else 'before F'}); USB "
             f"serial (DEVICEID) {s['deviceid1']:08X}{s['deviceid0']:08X}"),
            ("CTRL-AP APPROTECTSTATUS", "PASS" if ap == 1 else "FAIL", f"{ap} (1: SWD open)"),
            ("UICR now", "INFO", "erased (a blank chip)" if erased else ", ".join(
                f"{k} 0x{s[k]:08X}" for k in ("pselreset0", "pselreset1", "approtect", "nfcpins", "regout0", "nrffw0",
                                              "nrffw1")))]
    want = uicr_words(fxx)
    done = all(s[k] == want.get(STATE[k], 0xFFFFFFFF) for k in ("pselreset0", "pselreset1", "approtect", "nfcpins",
                                                                 "regout0")) and \
        (s["nrffw0"], s["nrffw1"]) == (0xF4000, 0xFE000)
    return rows, ("flashed already (UICR and bootloader as N5's): swd.py diag, or unplug the Pico for the first boot"
                  if done else "swd.py flash --bootloader <nice_nano_bootloader-0.10.0_s140_6.1.1.hex>")


def check_cmds():
    return ["readap 1 0x0c"] + [f"read32 0x{a:08x}" for a in STATE.values()] + ["status"]


def cmd_check(uid):
    need_probe(uid)
    out = commander(uid, "attach", check_cmds())  # attach: the CPU is neither halted nor reset
    rows, nxt = check_rows(out)
    m = re.search(r"^Core \d+ \(.*\):\s+(\w+)", out, re.M)
    rows.append(("CPU", "INFO", m.group(1) if m else "not read"))
    table(rows)
    print(f"\nnext: {nxt}")
    return 0 if rows[0][1] == "PASS" and rows[1][1] == "PASS" else 1


# ---- flash ------------------------------------------------------------------------------------------------------

def readback_rows(out, fxx, diffs):
    """Rows after the load: the UICR words, NRFFW, CTRL-AP, halted, and the byte compare (diffs: {name: count})"""
    got, ap = dict(reads(out)), ap_status(out)
    ok = lambda b: "PASS" if b else "FAIL"
    want = {**uicr_words(fxx), **NRFFW}
    if not fxx:
        want[0x10001208] = 0xFFFFFFFF
    rows = [(f"0x{a:08X}", ok(got.get(a) == v), f"0x{got[a]:08X} (want 0x{v:08X})" if a in got else "not read")
            for a, v in sorted(want.items())]
    rows.append(("CTRL-AP APPROTECTSTATUS", ok(ap == 1), f"{ap} (1: open)"))
    rows.append(("CPU", ok(re.search(r"^Core \d+ \(.*\):\s+Halted", out, re.M) is not None),
                 "halted: nothing has run"))
    for name, n in diffs.items():
        rows.append((f"bytes of {name}", ok(n == 0), f"{n} differ" if n >= 0 else "not read back"))
    return rows


def compare(uid, files, out_dir):
    """Every byte the HEX files define, read back (savemem) and compared: {file name: bytes that differ, -1 unread}"""
    segs = {f: hex_segments(f) for f in files}
    cmds, saved = [], []
    for f, ss in segs.items():
        for a, data in ss.items():
            p = f"{out_dir}/readback-{a:08x}.bin"
            cmds.append(f"savemem 0x{a:x} {len(data)} {p}")
            saved.append((f, p, data))
    commander(uid, "halt", cmds)
    diffs = {os.path.basename(f): 0 for f in files}
    for f, p, data in saved:
        try:
            back = open(p, "rb").read()
        except OSError:
            diffs[os.path.basename(f)] = -1
            continue
        diffs[os.path.basename(f)] += sum(x != y for x, y in zip(back, data)) + abs(len(back) - len(data))
    return diffs


def cmd_flash(uid, bootloader, unlock, out_dir):
    need_probe(uid)
    digest = hashlib.sha256(open(bootloader, "rb").read()).hexdigest()
    if digest != BOOTLOADER_SHA256:
        sys.exit(f"{bootloader}: sha256 {digest}, not the nice!nano 0.10.0 bootloader ({BOOTLOADER_SHA256})")
    out = commander(uid, "halt", check_cmds())
    if state(out)["part"] is None:
        if not locked(out):
            sys.exit("no answer over SWD (swd.py check says more):\n" + out.strip()[-500:])
        if not unlock:
            sys.exit("the chip is locked (APPROTECT). If this is a NEW chip, run again with --unlock: a mass erase "
                     "(flash, UICR, RAM). Never on a board with recordings or settings on it; " + FLASHED_LOCKED)
        print("locked: mass erase through CTRL-AP (--unlock). Keep the board powered until the end")
        print(pyocd(["erase", "-u", uid, *TARGET, "--mass", "-O", "auto_unlock=true"], check=True).strip()[-500:])
        out = commander(uid, "halt", check_cmds())
        if state(out)["part"] is None:
            sys.exit("still no access after the mass erase; stop and note this down:\n" + out.strip()[-500:])
    s = state(out)
    if supply_problem(s):
        sys.exit(f"not loading: the chip sees {supply_problem(s)}. The first flash is M11 = A, the Pico's 3V3 on VDD "
                 "with the battery and the board's USB unplugged: unplug them and run flash again")
    variant, fxx = build_code(s)
    os.makedirs(out_dir, exist_ok=True)
    uicr = f"{out_dir}/uicr-n5{'-fxx-engineering' if fxx else ''}.hex"
    open(uicr, "w").write(intel_hex(uicr_words(fxx)))
    print(f"chip {variant} ({'build code F or later' if fxx else 'before F'}), USB serial "
          f"{s['deviceid1']:08X}{s['deviceid0']:08X}; UICR file {uicr} sha256 "
          f"{hashlib.sha256(open(uicr, 'rb').read()).hexdigest()}")
    # the bootloader HEX may define UICR words too (NRFFW): none of N5's UICR words, or one load would write both
    boot = hex_segments(bootloader)
    clash = [a for a in uicr_words(fxx) for s0, d in boot.items() if s0 <= a < s0 + len(d)]
    if clash:
        sys.exit(f"the bootloader HEX writes UICR words of N5's own: {[hex(a) for a in clash]}")
    # one load for both: pyOCD erases the whole UICR page for either file, so two loads would lose the first's words.
    # halt, no reset before or after: the chip has not run and does not (an F chip would lock again on a pin reset)
    print(pyocd(["load", "-u", uid, *TARGET, *NO_UNLOCK, "--connect", "halt", "--no-reset", "-O", "load.pre_reset=off",
                 bootloader, uicr], check=True).strip()[-500:])
    out = commander(uid, "halt", ["readap 1 0x0c"] + [f"read32 0x{a:08x}" for a in sorted({**uicr_words(True), **NRFFW})]
                    + ["status"])
    rows = readback_rows(out, fxx, compare(uid, [bootloader, uicr], out_dir))
    open(f"{out_dir}/flash-{s['deviceid1']:08X}{s['deviceid0']:08X}.txt", "w").write(out)
    failed = table(rows)
    if failed:
        print("\nFAIL: do not power the board from its battery or USB. Run flash once more (the same one load); "
              "if it fails again, stop and keep this output")
        return 1
    print("\nPASS. 现在拔掉 Pico 的全部线，再接电池。\n（芯片里只有 bootloader：第一个应用走 UF2，B 面 D2 焊好后插板子的 USB，出现 NICENANO 盘后 "
          "selftest.py flash SERIAL --n5。B 面 Q1 / D3 没焊时接电池不一定能上电。）")
    return 0


# ---- diag -------------------------------------------------------------------------------------------------------

P = lambda port, pin: (port, pin)
GPIO_BASE = {0: 0x50000000, 1: 0x50000300}
IN, CNF = 0x510, 0x700
C_DEFAULT, C_NOPULL, C_DOWN, C_UP = 0x2, 0x0, 0x4, 0xC

# LR-N5's balls with nothing on them but a debug pad at most (UART TX P0.13 / RX P0.17, debug port pins 8 / 10): the
# hardware's netlist (BALL table), as the self-test firmware's list. P0.09 / P0.10 only once NFCPINS makes them GPIOs
FREE = [P(0, 3), P(0, 4), P(0, 5), P(0, 7), P(0, 9), P(0, 10), P(0, 13), P(0, 14), P(0, 16), P(0, 17), P(0, 21), P(0, 25),
        P(0, 26), P(0, 27), P(0, 28), P(0, 30), P(1, 1), P(1, 2), P(1, 3), P(1, 4), P(1, 5), P(1, 6), P(1, 7), P(1, 9),
        P(1, 10), P(1, 11), P(1, 12), P(1, 14)]
# Enables with a pull-down resistor: pre-charged high by the 13k pull-up for a moment, released, read: the resistor
# brings it low; an open ball keeps the charge, as the free balls in CONTROL do (the lab's 11-11 method)
ENABLES = {P(0, 29): "MEM_EN (R25 1M)", P(0, 12): "MIC_PWR (R31 100k)"}
CONTROL = [P(1, 9), P(1, 1)]
# Lines on parts: read with no pull only, never pulled up (a part without power would be fed through the pin)
PARTS = {P(0, 20): "MEM_CS", P(0, 22): "MEM_DI", P(0, 24): "MEM_SCK", P(1, 0): "MEM_DO", P(0, 6): "MIC_DAT",
         P(0, 8): "PDM_CLK", P(0, 23): "IMU_SDA", P(0, 19): "IMU_SCL", P(0, 11): "IMU_INT1", P(1, 8): "IMU_VDD",
         P(0, 31): "CHRG", P(0, 15): "LED_G", P(1, 13): "LED_R", P(1, 15): "SW", P(0, 2): "VBAT_SENSE"}

SAADC, RAM = 0x40007000, 0x2003FFF0
SAADC_IN = {"VDD": 9, "VDDHDIV5": 0x0D, "AIN0": 1}  # CH[n].PSELP


def pin(p):
    return f"P{p[0]}.{p[1]:02d}"


def cnf(p, v):
    return f"write32 0x{GPIO_BASE[p[0]] + CNF + 4 * p[1]:08x} 0x{v:x}"


def read_ins():
    return [f"read32 0x{GPIO_BASE[0] + IN:08x}", f"read32 0x{GPIO_BASE[1] + IN:08x}"]


REGS = {"RESETREAS": 0x40000400, "USBREGSTATUS": 0x40000438, "POFCON": 0x40000510, "DCDCEN": 0x40000578,
        "DCDCEN0": 0x40000580, "MAINREGSTATUS": 0x40000640, "NFCPINS": 0x1000120C, "REGOUT0": 0x10001304}


def diag_cmds(free, ram_word=0):
    """The commander script: (commands, the label of each read32 in order). CPU halted; only GPIO and SAADC registers
    are written, left at their reset values at the end (not at what they held before), and the RAM word the sample lands
    in (top of RAM: the bootloader's stack), put back to ram_word"""
    cmds, labels = [], []

    def rd(addr, label):
        cmds.append(f"read32 0x{addr:08x}")
        labels.append(label)
    for k, a in {**FICR, **REGS}.items():
        rd(a, k)
    # SAADC: 12 bit, gain 1/6 on the 0.6 V reference (0-3.6 V), 40 us acquisition (AIN0 sees 500k), one sample to RAM
    cmds += [f"write32 0x{SAADC + 0x500:08x} 0", f"write32 0x{SAADC + 0x5F0:08x} 2", f"write32 0x{SAADC + 0x5F4:08x} 0",
             f"write32 0x{SAADC + 0x5F8:08x} 0", f"write32 0x{SAADC + 0x514:08x} 0",
             f"write32 0x{SAADC + 0x518:08x} 0x50000", f"write32 0x{SAADC + 0x62C:08x} 0x{RAM:x}",
             f"write32 0x{SAADC + 0x630:08x} 1", f"write32 0x{SAADC + 0x500:08x} 1",
             f"write32 0x{SAADC + 0x110:08x} 0", f"write32 0x{SAADC + 0x00C:08x} 1", "sleep 20"]
    rd(SAADC + 0x110, "CALIBRATEDONE")
    for name, sel in SAADC_IN.items():
        cmds += [f"write32 0x{SAADC + 0x510:08x} {sel}", f"write32 0x{RAM:08x} 0x7fff7fff",
                 f"write32 0x{SAADC + 0x104:08x} 0", f"write32 0x{SAADC + 0x000:08x} 1", "sleep 2",
                 f"write32 0x{SAADC + 0x004:08x} 1", "sleep 5"]
        rd(SAADC + 0x104, f"END {name}")
        rd(RAM, f"ADC {name}")
    cmds += [f"write32 0x{SAADC + 0x008:08x} 1", "sleep 2", f"write32 0x{SAADC + 0x500:08x} 0",
             f"write32 0x{SAADC + 0x510:08x} 0", f"write32 0x{SAADC + 0x518:08x} 0x20000",
             f"write32 0x{SAADC + 0x62C:08x} 0", f"write32 0x{SAADC + 0x630:08x} 0"]
    # free balls: all pulled up, read; all pulled down, read
    for mode, tag in ((C_UP, "up"), (C_DOWN, "down")):
        cmds += [cnf(p, mode) for p in free] + ["sleep 5"]
        for port in (0, 1):
            rd(GPIO_BASE[port] + IN, f"IN{port} {tag}")
    cmds += [cnf(p, C_DEFAULT) for p in free]
    # enables and controls: pre-charge high, release, read after 2 ms
    rel = list(ENABLES) + CONTROL
    cmds += [cnf(p, C_UP) for p in rel] + ["sleep 3"] + [cnf(p, C_NOPULL) for p in rel] + ["sleep 2"]
    for port in (0, 1):
        rd(GPIO_BASE[port] + IN, f"IN{port} released")
    cmds += [cnf(p, C_DOWN) for p in CONTROL] + ["sleep 3"] + [cnf(p, C_NOPULL) for p in CONTROL] + ["sleep 2"]
    for port in (0, 1):
        rd(GPIO_BASE[port] + IN, f"IN{port} released low")
    cmds += [cnf(p, C_DEFAULT) for p in rel]
    # lines on parts, no pull
    cmds += [cnf(p, C_NOPULL) for p in PARTS] + ["sleep 2"]
    for port in (0, 1):
        rd(GPIO_BASE[port] + IN, f"IN{port} parts")
    cmds += [cnf(p, C_DEFAULT) for p in PARTS] + [f"write32 0x{RAM:08x} 0x{ram_word:x}", "status"]
    return cmds, labels


def adc_mv(raw):
    raw = raw & 0xFFFF
    raw -= 0x10000 if raw & 0x8000 else 0
    return max(raw, 0) * 3600 // 4096


RESETREAS_BITS = ["RESETPIN", "DOG", "SREQ", "LOCKUP"] + [None] * 12 + ["OFF", "LPCOMP", "DIF", "NFC", "VBUS"]


def diag_rows(v, usb=None, probe=None):
    """Rows from the labelled reads v; usb: the board's USB plugged in (None: not said); probe: the Pico's ADC in mV
    ({"VDD", "VSYS", "VBUS"} at the test points, dividers applied) or None"""
    rows = []
    ok = lambda b: "PASS" if b else "FAIL"
    bit = lambda port_word, p: v[port_word.format(p[0])] >> p[1] & 1
    rr = v["RESETREAS"]
    rows.append(("RESETREAS", "INFO", f"0x{rr:08X}: " + (", ".join(n for i, n in enumerate(RESETREAS_BITS) if n and rr >> i & 1)
                                                         or "none set: power-on or brown-out reset")))
    vbus = v["USBREGSTATUS"] & 1
    main = v["MAINREGSTATUS"] & 1
    pv = probe or {}
    if "VBUS" in pv:
        want = pv["VBUS"] > 4000
        rows.append(("USBREGSTATUS.VBUSDETECT (AD2)", ok(vbus == want), f"{vbus}; the probe reads VBUS {pv['VBUS']} mV "
                     "at TP1" + ("" if vbus == want else ": the chip disagrees, check U1's AD2 ball (VBUS)")))
    elif usb is not None:
        rows.append(("USBREGSTATUS.VBUSDETECT (AD2)", ok(vbus == usb), f"{vbus} with the board's USB "
                     f"{'plugged in (1 expected; 0: U1 ball AD2 open or no 5 V at TP1)' if usb else 'out (0 expected)'}"))
    else:
        rows.append(("USBREGSTATUS.VBUSDETECT (AD2)", "INFO", f"{vbus} (1 when the board's USB is plugged in; say "
                     "--usb yes/no to judge it)"))
    vdd, vddh, vbat = adc_mv(v["ADC VDD"]), adc_mv(v["ADC VDDHDIV5"]) * 5, adc_mv(v["ADC AIN0"]) * 2
    saadc_ok = v["CALIBRATEDONE"] and all(v[f"END {n}"] for n in SAADC_IN)
    rows.append(("MAINREGSTATUS", "INFO", f"{main} ({'high-voltage mode: VDDH supplied, REG0 makes VDD' if main else
                 'normal mode: VDD supplied directly (the Pico), VDDH not used'})"))
    rows.append(("POFCON", "INFO", f"0x{v['POFCON']:08X} (power-fail comparator {'on' if v['POFCON'] & 1 else 'off'}"
                 f"; threshold field {v['POFCON'] >> 1 & 0xF}, VDDH {v['POFCON'] >> 8 & 0xF})"))
    rows.append(("DCDCEN / DCDCEN0", "INFO", f"{v['DCDCEN'] & 1} / {v['DCDCEN0'] & 1} (REG1 / REG0 on their DCDC; the "
                 "first boards stay on the LDOs)"))
    if not saadc_ok:
        rows.append(("SAADC", "FAIL", "no conversion finished (calibration or END not seen): readings below not valid"))
    rows.append(("VDD (SAADC)", ok(saadc_ok and 2700 <= vdd <= 3450), f"{vdd} mV (2700-3450: the Pico's 3.3 V, or REG0's "
                 "3.0 V)" + (f"; probe {pv['VDD']} mV at VDD, {vdd - pv['VDD']:+d}" if "VDD" in pv else "")))
    vddh_row = f"{vddh} mV (VSYS on Y2; about 0 before D2 / the battery path is fitted)"
    verdict = "INFO"
    if "VSYS" in pv:
        vddh_row += f"; probe {pv['VSYS']} mV at TP11 VSYS"
        if pv["VSYS"] > 3000:
            verdict = ok(abs(vddh - pv["VSYS"]) < 300)
            vddh_row += "" if verdict == "PASS" else ": VSYS is there but the chip does not see it, check U1's Y2 ball"
    if main and vddh < 2500:
        verdict, vddh_row = "FAIL", vddh_row + ": high-voltage mode with no VDDH, check U1's Y2 ball"
    rows.append(("VDDH (SAADC VDDHDIV5 x5)", verdict, vddh_row))
    if vddh > 2000:
        rows.append(("power", "LOOK", "VDDH is up: if the Pico's 3V3 is still on VDD, unplug the board's USB / battery "
                     "now (REG0 and the Pico would both drive VDD)"))
    rows.append(("battery (AIN0 x2, R33/R34)", "INFO", f"{vbat} mV (about 0 without a cell or before R33/R34)"))
    nfc_gpio = not v["NFCPINS"] & 1
    free = [p for p in FREE if nfc_gpio or p not in (P(0, 9), P(0, 10))]
    low = [pin(p) for p in free if not bit("IN{} up", p)]
    high = [pin(p) for p in free if bit("IN{} down", p)]
    rows.append(("free balls, pulled up", ok(not low), f"{len(free)} balls; reading 0 (to GND, or a bridge to a grounded "
                 f"net): {', '.join(low) or 'none'}"))
    rows.append(("free balls, pulled down", ok(not high), f"reading 1 (to VDD, or a bridge to a powered net): "
                 f"{', '.join(high) or 'none'}" + ("" if nfc_gpio else "; P0.09/P0.10 skipped (NFC mode)")))
    control = all(bit("IN{} released", p) and not bit("IN{} released low", p) for p in CONTROL)
    for p, name in ENABLES.items():
        held = bit("IN{} released", p)
        rows.append((f"{name} {pin(p)}", ok(not held) if control else "INFO", "pulled low by its resistor once "
                     "released" if not held else "kept high once released: the ball or the resistor is open"
                     + ("" if control else "; not judged, the free control balls did not hold a level")))
    rows.append(("lines on parts (no pull)", "INFO", ", ".join(f"{n} {bit('IN{} parts', p)}" for p, n in PARTS.items())
                 + " (unpowered parts: mostly 0; MEM_CS follows MEM_VDD through R26)"))
    return rows


def probe_adc(port):
    """The lr-probe Pico's own ADC (its second serial port, lines "D,ms,mV26,mV27,mV28,..."): VDD on GP26 through
    10k (x1), VSYS on GP27 and VBUS on GP28 through 100k/100k (x2), each the median of a second's lines"""
    import serial
    from serial.tools import list_ports
    if port == "auto":  # the probe's two serial ports: the ADC one sorts last (lr-probe REPORT)
        ports = sorted(p.device for p in list_ports.comports() if p.vid == 0x2E8A and p.pid == 0x000C)
        if len(ports) != 2:
            sys.exit(f"--adc auto: expected the probe's two serial ports, found {ports}; give --adc PORT")
        port = ports[-1]
    s = serial.Serial(port, timeout=0.3)  # opening raises DTR, which starts its lines
    rows, end = [], time.time() + 1.5
    while time.time() < end:
        f = s.readline().decode(errors="replace").strip().split(",")
        if len(f) >= 5 and f[0] == "D":
            rows.append([int(x) for x in f[2:5]])
    s.close()
    if not rows:
        sys.exit(f"{port}: no ADC lines from the probe (is it lr-probe, and its rate not 0?)")
    med = lambda i: sorted(r[i] for r in rows)[len(rows) // 2]
    return {"VDD": med(0), "VSYS": med(1) * 2, "VBUS": med(2) * 2}


def cmd_diag(uid, usb, adc, out_dir):
    need_probe(uid)
    out = commander(uid, "halt", ["readap 1 0x0c", "read32 0x1000120c", f"read32 0x{RAM:08x}"])
    got = dict(reads(out))
    if ap_status(out) != 1 or RAM not in got:
        sys.exit("SWD is closed (APPROTECT) or not answering: swd.py check says more")
    nfc_gpio = not got.get(0x1000120C, 1) & 1
    cmds, labels = diag_cmds([p for p in FREE if nfc_gpio or p not in (P(0, 9), P(0, 10))], got[RAM])
    probe = probe_adc(adc) if adc else None
    out = commander(uid, "halt", cmds)
    vals = [x[1] for x in reads(out)]
    if len(vals) != len(labels):
        sys.exit(f"read {len(vals)} of {len(labels)} words; pyocd said:\n{out.strip()[-800:]}")
    v = dict(zip(labels, vals))
    serial = f"{v['deviceid1']:08X}{v['deviceid0']:08X}"
    rows = diag_rows(v, usb, probe)
    print(f"LR-N5 {serial}, {datetime.datetime.now():%Y-%m-%d %H:%M:%S}" + (f"; probe ADC {probe}" if probe else ""))
    failed = table(rows)
    os.makedirs(out_dir, exist_ok=True)
    fn = f"{out_dir}/diag-{serial}-{datetime.datetime.now():%H%M%S}.json"
    json.dump({"serial": serial, "reads": v, "probe": probe, "rows": rows}, open(fn, "w"), indent=1)
    print(f"\n{'FAIL: ' + ', '.join(failed) if failed else 'PASS'}; saved {fn}. The CPU stays halted: unplug the Pico "
          "(power off) before the board runs on its own")
    return 1 if failed else 0


# ---- offline test -----------------------------------------------------------------------------------------------

def test():
    # LR-N4's UICR files (the hardware repo, hand-checked, sha256 87bef184... and 47757894...) from the same writer
    n4 = {0x10001200: 0x12, 0x10001204: 0x12, 0x10001304: 0xFFFFFFFC}
    assert intel_hex(n4) == ":020000041000EA\n:081200001200000012000000C2\n:04130400FCFFFFFFEC\n:00000001FF\n"
    fx = intel_hex({**n4, 0x10001208: 0x5A})
    assert hashlib.sha256(fx.encode()).hexdigest() == \
        "47757894465d3286fbccaaf128614bff9575ac6f9625ff9f3eb5e8fa95d1a1ae", fx
    assert hashlib.sha256(intel_hex(n4).encode()).hexdigest().startswith("87bef184")
    # N5's: NFCPINS joins the run; back through intelhex to the same words
    import tempfile
    for fxx in (False, True):
        with tempfile.NamedTemporaryFile("w", suffix=".hex", delete=False) as f:
            f.write(intel_hex(uicr_words(fxx)))
        segs = hex_segments(f.name)
        os.unlink(f.name)
        words = {s + i: int.from_bytes(d[i:i + 4], "little") for s, d in segs.items() for i in range(0, len(d), 4)}
        assert words == uicr_words(fxx), words
    print(intel_hex(uicr_words(True)), end="")
    print("intel_hex: ok (LR-N4's files byte for byte; N5's words back through intelhex)")

    # check: a blank Fxx chip, then a flashed one, then a locked one (pyOCD 0.45.1's formats)
    blank = {0x10000100: 0x52840, 0x10000104: 0x41414630, 0x10000060: 0x89ABCDEF, 0x10000064: 0x01234567}
    dump = lambda w: "AP register 0x100000c = 0x00000001\n" + "".join(f"{a:08x}:  {x:08x}    |....|\n" for a, x in w.items())
    erased = {a: 0xFFFFFFFF for a in STATE.values() if a not in blank}
    rows, nxt = check_rows(dump({**blank, **erased}))
    assert [r[1] for r in rows] == ["PASS", "PASS", "INFO"] and "erased" in rows[2][2] and "flash --bootloader" in nxt
    assert "F or later" in rows[0][2] and rows[0][2].endswith("0123456789ABCDEF")
    flashed = {**erased, **uicr_words(True), **NRFFW}
    assert check_rows(dump({**blank, **flashed}))[1].startswith("flashed already")
    rows, nxt = check_rows("Error: The target is locked, so cannot connect (APPROTECT)")
    assert rows[0][1] == "FAIL" and "--unlock" in nxt and "do NOT use --unlock" in nxt
    pico = state(dump({**blank, **erased, 0x40000438: 0, 0x40000640: 0}))
    assert supply_problem(pico) is None
    assert supply_problem({**pico, "mainregstatus": 1}).startswith("VDDH")
    assert supply_problem({**pico, "usbregstatus": 1}).startswith("VBUS")
    assert supply_problem({**pico, "usbregstatus": None}).startswith("VBUS")  # not read: not judged as off
    assert "check the wires" in check_rows("No ACK")[1]
    print("check_rows, supply_problem: ok")

    # readback: right, then REGOUT0 erased and one byte off
    out = dump({**flashed}) + "Core 0 (cortex_m):  Halted\n"
    assert not [r for r in readback_rows(out, True, {"bl.hex": 0, "uicr.hex": 0}) if r[1] != "PASS"]
    bad = dump({**flashed, 0x10001304: 0xFFFFFFFF}) + "Core 0 (cortex_m):  Running\n"
    assert [r[0] for r in readback_rows(bad, True, {"bl.hex": 1, "uicr.hex": -1}) if r[1] == "FAIL"] == \
        ["0x10001304", "CPU", "bytes of bl.hex", "bytes of uicr.hex"]
    dxx = {**erased, **uicr_words(False), **NRFFW}  # pre-F: APPROTECT must stay erased
    assert not [r for r in readback_rows(dump(dxx) + "Core 0 (cortex_m):  Halted\n", False, {}) if r[1] != "PASS"]
    print("readback_rows: ok")

    # diag: a good board on the Pico (no VDDH, USB in), then AD2 open, a ball to GND, MEM_EN's resistor missing
    cmds, labels = diag_cmds(FREE)
    assert len([c for c in cmds if c.startswith("read32")]) == len(labels) and cmds[-1] == "status"
    assert diag_cmds(FREE, 0x1234)[0][-2] == f"write32 0x{RAM:08x} 0x1234"  # the borrowed RAM word put back
    assert all(c.split()[0] in ("read32", "write32", "sleep", "status") for c in cmds)
    mv = lambda m: -(-m * 4096 // 3600)  # the raw sample that reads back as m mV
    good = {k: 0 for k in labels}
    good.update({"deviceid0": 1, "deviceid1": 2, "RESETREAS": 0, "USBREGSTATUS": 1, "MAINREGSTATUS": 0,
                 "NFCPINS": 0xFFFFFFFE, "CALIBRATEDONE": 1, "END VDD": 1, "END VDDHDIV5": 1, "END AIN0": 1,
                 "ADC VDD": mv(3300), "ADC VDDHDIV5": 0, "ADC AIN0": 0})
    mask = lambda ps, port: sum(1 << p[1] for p in ps if p[0] == port)
    for port in (0, 1):
        good[f"IN{port} up"] = mask(FREE, port)
        good[f"IN{port} released"] = mask(CONTROL, port)  # the controls hold high, the enables fall
    rows = diag_rows(good, usb=True)
    assert not [r for r in rows if r[1] == "FAIL"], rows
    assert rows[1][1] == "PASS" and "3300 mV" in [r for r in rows if r[0] == "VDD (SAADC)"][0][2]
    bad = {**good, "USBREGSTATUS": 0, "IN0 up": good["IN0 up"] & ~(1 << 21), "IN0 released": good["IN0 released"] | 1 << 29}
    assert [r[0] for r in diag_rows(bad, usb=True) if r[1] == "FAIL"] == \
        ["USBREGSTATUS.VBUSDETECT (AD2)", "free balls, pulled up", "MEM_EN (R25 1M) P0.29"]
    probe = {"VDD": 3290, "VSYS": 4700, "VBUS": 5050}  # the chip sees no VSYS that the probe sees: Y2
    r = {x[0]: x for x in diag_rows({**good, "ADC VDDHDIV5": 0}, probe=probe)}
    assert r["VDDH (SAADC VDDHDIV5 x5)"][1] == "FAIL" and r["USBREGSTATUS.VBUSDETECT (AD2)"][1] == "PASS"
    r = {x[0]: x for x in diag_rows({**good, "ADC VDDHDIV5": mv(940)}, probe=probe)}
    assert r["VDDH (SAADC VDDHDIV5 x5)"][1] == "PASS" and r["power"][1] == "LOOK"
    assert adc_mv(0xFFF0) == 0 and adc_mv(4095) == 3599  # a negative sample (offset) reads 0
    print("diag_rows: ok")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["check", "flash", "diag", "uicr", "test"])
    ap.add_argument("-u", "--uid", help="the Pico probe's unique ID (pyocd list)")
    ap.add_argument("--bootloader", help="flash: nice_nano_bootloader-0.10.0_s140_6.1.1.hex")
    ap.add_argument("--unlock", action="store_true", help="flash: a locked chip is mass-erased first (new chips only)")
    ap.add_argument("--usb", choices=["yes", "no"], help="diag: the board's USB is plugged in (judges VBUSDETECT)")
    ap.add_argument("--adc", help="diag: the lr-probe's ADC serial port, or auto")
    ap.add_argument("--out", default="swd-out", help="flash, diag: where the UICR file, read-backs and reports go")
    ap.add_argument("--fxx", action="store_true", help="uicr: build code F or later (APPROTECT 0x5A)")
    ap.add_argument("-o", help="uicr: the file to write")
    a = ap.parse_args()
    if a.action == "test":
        sys.exit(test())
    if a.action == "uicr":
        if not a.o:
            ap.error("uicr: -o FILE")
        open(a.o, "w").write(intel_hex(uicr_words(a.fxx)))
        sys.exit(print(f"{a.o} sha256 {hashlib.sha256(open(a.o, 'rb').read()).hexdigest()}"))
    if not a.uid:
        ap.error("-u PROBE: the Pico's unique ID, as pyocd list shows it")
    if a.action == "check":
        sys.exit(cmd_check(a.uid))
    if a.action == "flash":
        if not a.bootloader:
            ap.error("flash: --bootloader HEX")
        sys.exit(cmd_flash(a.uid, a.bootloader, a.unlock, a.out))
    sys.exit(cmd_diag(a.uid, None if a.usb is None else a.usb == "yes", a.adc, a.out))
