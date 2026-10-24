"""Firmware update over Bluetooth: the running firmware is told to reboot into its bootloader's BLE DFU mode (Adafruit
nRF52 bootloader, Nordic legacy DFU), then a package made by `lr.sh pkg` is sent. The phone app does the same with
Nordic's DFU library. If a transfer fails midway, do not reset the bootloader (CP 0x06): with no valid app it comes
back in USB-only DFU mode (10-03) and needs `lr.sh flash` over USB.
    uv run --no-project --with bleak python lr_dfu.py PACKAGE.zip --name LR-P3-XXXX
"""
import asyncio
import json
import struct
import sys
import time
import zipfile

from bleak import BleakClient, BleakScanner

U = lambda n: f"0000{n:04x}-1212-efde-1523-785feabcd123"
DFU_SVC, CP, PKT = U(0x1530), U(0x1531), U(0x1532)
PRN = 8  # (16 lost the XIAO bootloader mid-image, 10-03) the bootloader reports the bytes it has every PRN packets: our flow control
PKT_LEN = 20  # legacy DFU's packet size. 240-byte packets (MTU 247) stalled the Adafruit bootloader mid-image (10-03)


async def enter_bootloader(name: str, uf2=False):
    dev = await BleakScanner.find_device_by_filter(lambda d, a: (a.local_name or "").startswith(name), 20)  # by name: any firmware version
    if not dev:
        sys.exit(f"no {name}")
    print(f"{dev.name}: rebooting into the bootloader")
    async with BleakClient(dev) as c:
        try:
            await c.write_gatt_char(CP, b"\x57" if uf2 else b"\x06", response=True)  # 0x57: the USB (UF2) bootloader
        except Exception:
            pass  # it resets before it answers


async def send(pkg: str):
    z = zipfile.ZipFile(pkg)
    app = json.loads(z.read("manifest.json"))["manifest"]["application"]
    fw, init = z.read(app["bin_file"]), z.read(app["dat_file"])
    # the bootloader advertises the DFU service under its own name; the running firmware has it too, named LR-...
    dev = await BleakScanner.find_device_by_filter(lambda d, a: DFU_SVC in a.service_uuids and not (a.local_name or "").startswith("LR-"), 30)
    if not dev:
        sys.exit("no device in DFU mode")
    print(f"bootloader: {dev.name} {dev.address}")
    async with BleakClient(dev) as c:
        q: asyncio.Queue = asyncio.Queue()
        await c.start_notify(CP, lambda _, d: q.put_nowait(bytes(d)))

        async def resp(op: int, timeout=60):
            while True:
                r = await asyncio.wait_for(q.get(), timeout)
                if r[0] == 0x10 and r[1] == op:
                    if r[2] != 1:
                        sys.exit(f"DFU op {op} failed: status {r[2]}")
                    return

        async def write(data: bytes):
            for i in range(0, len(data), PKT_LEN):
                await c.write_gatt_char(PKT, data[i:i + PKT_LEN], response=False)

        await c.write_gatt_char(CP, b"\x01\x04", response=True)  # start, application
        await c.write_gatt_char(PKT, struct.pack("<III", 0, 0, len(fw)), response=False)
        await resp(1, 120)  # after the erase
        await c.write_gatt_char(CP, b"\x02\x00", response=True)
        await write(init)
        await c.write_gatt_char(CP, b"\x02\x01", response=True)
        await resp(2)
        await c.write_gatt_char(CP, bytes([0x08, PRN, 0]), response=True)
        await c.write_gatt_char(CP, b"\x03", response=True)
        t, sent = time.time(), 0
        for n, i in enumerate(range(0, len(fw), PKT_LEN), 1):
            await c.write_gatt_char(PKT, fw[i:i + PKT_LEN], response=False)
            sent = min(i + PKT_LEN, len(fw))
            if n % PRN == 0 and sent < len(fw):
                while (r := await asyncio.wait_for(q.get(), 30))[0] != 0x11:
                    pass
                if sent // 65536 != (sent - PKT_LEN * PRN) // 65536:
                    print(f"  {sent} of {len(fw)} B, {sent / 1024 / (time.time() - t):.1f} KB/s")
        await resp(3)
        print(f"sent {len(fw)} B in {time.time() - t:.0f} s")
        await c.write_gatt_char(CP, b"\x04", response=True)
        await resp(4)
        try:
            await c.write_gatt_char(CP, b"\x05", response=True)  # activate and reset
        except Exception:
            pass
    print("done: the new firmware is starting")


if __name__ == "__main__":
    if "--uf2" in sys.argv:  # lr.sh flash: into the USB drive bootloader, for a firmware without a serial port
        sys.exit(asyncio.run(enter_bootloader(sys.argv[sys.argv.index("--name") + 1], uf2=True)))
    pkg = sys.argv[1]
    if "--name" in sys.argv:
        asyncio.run(enter_bootloader(sys.argv[sys.argv.index("--name") + 1]))
    asyncio.run(send(pkg))
