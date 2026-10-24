"""This Mac as a second phone near a real recorder, for uitest.sh (bleak): its adverts, and taking it over. Exit 0: done,
1: failed, 2: not found.
    uv run --no-project --with bleak python ble.py watch LR-P3-XXXX SECS OUT.jsonl   every advert: state byte, connectable
    uv run --no-project --with bleak python ble.py forget LR-P3-XXXX    pair (the Mac may ask to) and FORGET the other phones
    uv run --no-project --with bleak python ble.py enc LR-P3-XXXX       exit 0 if an encrypted read works (a bond it knows)
State byte (scan response, company 0xFFFF): bit0 bound, bit1 connected, bit2 paused (firmware transport.c lr_status)."""
import asyncio
import json
import sys
import time

from bleak import BleakClient, BleakScanner

TIME, CTRL = "7c3e0001-1a2b-4c5d-8e9f-6c6976657265", "7c3e0002-1a2b-4c5d-8e9f-6c6976657265"


async def watch(name, secs, out):
    def cb(d, a):
        if (a.local_name or d.name or "") == name:
            st = a.manufacturer_data.get(0xFFFF)
            conn = a.platform_data[1].get("kCBAdvDataIsConnectable") if len(a.platform_data) > 1 else None
            out.write(json.dumps({"ms": int(time.time() * 1000), "state": st[0] if st else None,
                                  "connectable": None if conn is None else bool(conn), "rssi": a.rssi}) + "\n")
            out.flush()
    async with BleakScanner(cb):
        await asyncio.sleep(secs)


async def session(name, fn):
    dev = await BleakScanner.find_device_by_filter(lambda d, a: (a.local_name or d.name or "") == name, 20)
    if not dev:
        print(f"{name} not found"); sys.exit(2)
    async with BleakClient(dev, timeout=25) as c:
        return await fn(c)


async def main(cmd, name, *a):
    if cmd == "watch":
        with open(a[1], "a") as out:
            await watch(name, float(a[0]), out)
        return 0
    if cmd == "forget":  # the encrypted write pairs first
        await session(name, lambda c: c.write_gatt_char(CTRL, b"\x05", response=True))
        print("FORGET sent"); return 0
    if cmd == "enc":
        try:
            await session(name, lambda c: c.read_gatt_char(TIME))
            print("encrypted read ok"); return 0
        except Exception as e:
            print("encrypted read failed:", type(e).__name__, e); return 1
    print(__doc__); return 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main(*sys.argv[1:])))
