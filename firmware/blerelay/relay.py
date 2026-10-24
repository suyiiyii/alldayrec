# /// script
# dependencies = ["bleak"]
# ///
"""The Mac's side of the ESP32 BLE relay (README.md): the boards under test are reached through it, so the Mac pairs
with none of them and lends no USB port. find() and client() take the place of bleak's
BleakScanner.find_device_by_filter() and BleakClient(), with the calls our test scripts make:

    async with Relay() as r:
        dev = await r.find(lambda d, a: a.local_name == "LR-P3-XXXX", 15, prefix="LR-P3")
        async with r.client(dev) as c:
            await c.read_gatt_char(FW_REV)

  uv run relay.py scan [PREFIX] [--secs S]     names, addresses, RSSI the relay hears
  uv run relay.py info NAME                    connect, list the characteristics, read Device Information, pair
"""
import argparse, asyncio, hashlib, hmac, os, struct, sys, uuid
from types import SimpleNamespace

from bleak import BleakClient, BleakScanner

SVC, CMD, EVT = (f"5aaa000{i}-610b-45d5-850b-509657eef28d" for i in (1, 2, 3))
KEY_FILE = os.environ.get("RELAY_KEY_FILE")  # the relay's key, 64 hex digits in a file outside the repository
HELLO, AUTH, SCAN, CONNECT, DISCONNECT, READ, WRITE, PAIR = 0x01, 0x02, 0x10, 0x11, 0x12, 0x13, 0x14, 0x15
EV_HELLO, EV_AUTH, EV_ADV, EV_SCAN_END, EV_CONNECTED, EV_ATTR, EV_ATTRS_END, EV_DISCONNECTED = (
    0x81, 0x82, 0x90, 0x91, 0x92, 0x93, 0x94, 0x95)
EV_READ, EV_WRITE, EV_NOTIFY, EV_ENC, EV_ERROR = 0x96, 0x97, 0x98, 0x99, 0xEE
CCCD, DECLS = 0x2902, (0x2800, 0x2801, 0x2802, 0x2803)


class RelayError(Exception):
    pass


def uuid16(u):
    return f"0000{u:04x}-0000-1000-8000-00805f9b34fb"


def parse_adv(data):
    """bleak's AdvertisementData fields from raw advertising data: local_name, service_uuids, manufacturer_data"""
    a = SimpleNamespace(local_name=None, service_uuids=[], manufacturer_data={})
    i = 0
    while i + 1 < len(data) and data[i]:
        n, t, v = data[i], data[i + 1], data[i + 2:i + 1 + data[i]]
        if t in (0x08, 0x09):
            a.local_name = v.decode(errors="replace")
        elif t in (0x02, 0x03):
            a.service_uuids += [uuid16(u) for (u,) in struct.iter_unpack("<H", v[:len(v) // 2 * 2])]
        elif t in (0x06, 0x07):
            a.service_uuids += [str(uuid.UUID(bytes=v[j:j + 16][::-1])) for j in range(0, len(v) - 15, 16)]
        elif t == 0xFF and len(v) >= 2:
            a.manufacturer_data[v[0] | v[1] << 8] = v[2:]
        i += 1 + n
    return a


class Relay:
    """One session with the relay. Its link to a board ends with this session (the relay drops it)."""

    async def __aenter__(self):
        if not KEY_FILE:
            raise RelayError("set RELAY_KEY_FILE to the relay's key file (README.md)")
        dev = await BleakScanner.find_device_by_filter(lambda d, a: SVC in a.service_uuids, 20)
        if not dev:
            raise RelayError("relay not found (is it powered?)")
        self.ble = BleakClient(dev)
        await self.ble.connect()
        self.q, self.notify, self.enc = asyncio.Queue(), {}, None
        await self.ble.start_notify(EVT, self._event)
        await self._send(HELLO)
        nonce = await self.wait(EV_HELLO, 5)
        key = bytes.fromhex(open(KEY_FILE).read().strip())
        await self._send(AUTH, hmac.new(key, nonce, hashlib.sha256).digest()[:16])
        if (await self.wait(EV_AUTH, 5)) != b"\x01":
            raise RelayError("relay refused the key")
        return self

    async def __aexit__(self, *exc):
        await self.ble.disconnect()

    def _event(self, _, frame):
        ev, p = frame[0], bytes(frame[1:])
        if ev == EV_NOTIFY:
            cb = self.notify.get(p[0] | p[1] << 8)
            return cb and cb(p[0] | p[1] << 8, bytearray(p[2:]))
        if ev == EV_ENC:
            self.enc = struct.unpack("<HBB", p)
        self.q.put_nowait((ev, p))

    async def _send(self, op, payload=b""):
        # all without response, so they stay in order (macOS queues writes with and without response apart)
        await self.ble.write_gatt_char(CMD, bytes([op]) + payload, response=False)

    async def wait(self, ev, timeout):
        """The payload of the next ev; an error or the board's disconnection raises"""
        async with asyncio.timeout(timeout):
            while True:
                e, p = await self.q.get()
                if e == ev:
                    return p
                if e == EV_ERROR:
                    raise RelayError(f"op 0x{p[0]:02x}: status 0x{p[1] | p[2] << 8:x}")
                if e == EV_DISCONNECTED:
                    raise RelayError(f"board disconnected: 0x{p[0] | p[1] << 8:x}")

    async def find(self, pred, secs, prefix=""):
        """The first device whose advertising (name starting with prefix) pred(device, adv) accepts, or None"""
        await self._send(SCAN, bytes([max(1, min(255, round(secs)))]) + prefix.encode())
        try:
            async with asyncio.timeout(secs + 2):
                while True:
                    e, p = await self.q.get()
                    if e == EV_SCAN_END:
                        return None
                    if e == EV_ADV:
                        a = parse_adv(p[8:])
                        a.rssi = struct.unpack("b", p[7:8])[0]
                        d = SimpleNamespace(addr_type=p[0], addr=p[1:7], name=a.local_name,
                                            address=":".join(f"{b:02X}" for b in p[6:0:-1]))
                        if pred(d, a):
                            return d
        except TimeoutError:
            return None
        finally:
            await self._send(SCAN, b"\x00")

    async def scan(self, secs, prefix=""):
        """Every device heard: {address: (name, best rssi)}"""
        seen = {}

        def keep(d, a):
            seen[d.address] = (d.name, max(a.rssi, seen.get(d.address, (0, -128))[1]))

        await self.find(lambda d, a: keep(d, a), secs, prefix)
        return seen

    def client(self, dev):
        return Peer(self, dev)


class Peer:
    """A board through the relay, as a BleakClient: read_gatt_char, write_gatt_char, start_notify, pair"""

    def __init__(self, relay, dev):
        self.r, self.dev = relay, dev

    async def __aenter__(self):
        r = self.r
        while not r.q.empty():
            r.q.get_nowait()  # what is left of the scan
        r.enc = None
        await r._send(CONNECT, bytes([self.dev.addr_type]) + self.dev.addr)
        status, self.mtu = struct.unpack("<HH", await r.wait(EV_CONNECTED, 15))
        if status:
            raise RelayError(f"connect: status 0x{status:x}")
        self.attrs = []
        async with asyncio.timeout(15):
            while (e := await r.q.get())[0] != EV_ATTRS_END:
                if e[0] == EV_ATTR:
                    p = e[1]
                    while p:  # [handle 2][type length][type]...
                        h, n, t = p[0] | p[1] << 8, p[2], p[3:3 + p[2]]
                        self.attrs.append((h, t[0] | t[1] << 8 if n == 2 else str(uuid.UUID(bytes=t[::-1]))))
                        p = p[3 + n:]
                elif e[0] == EV_DISCONNECTED:
                    raise RelayError("board disconnected during discovery")
            if status := e[1][2] | e[1][3] << 8:
                raise RelayError(f"discovery: status 0x{status:x}")
        return self

    async def __aexit__(self, *exc):
        await self.r._send(DISCONNECT)
        try:
            await self.r.wait(EV_DISCONNECTED, 5)
        except (TimeoutError, RelayError):
            pass

    def handle(self, char_uuid):
        """The value handle of a characteristic: the attribute of its type right after a characteristic declaration"""
        want = char_uuid.lower()
        for (_, prev), (h, t) in zip(self.attrs, self.attrs[1:]):
            if prev == 0x2803 and (uuid16(t) if isinstance(t, int) else t) == want:
                return h
        raise RelayError(f"no characteristic {char_uuid}")

    def characteristics(self):
        return [(h, uuid16(t) if isinstance(t, int) else t) for (_, prev), (h, t) in zip(self.attrs, self.attrs[1:])
                if prev == 0x2803]

    async def read_gatt_char(self, char_uuid):
        h = self.handle(char_uuid)
        await self.r._send(READ, struct.pack("<H", h))
        p = await self.r.wait(EV_READ, 30)
        if status := p[2] | p[3] << 8:
            raise RelayError(f"read {char_uuid}: status 0x{status:x}")
        return bytearray(p[4:])

    async def write_gatt_char(self, char_uuid, data, response=False):
        h = self.handle(char_uuid)
        await self.r._send(WRITE, struct.pack("<HB", h, response) + bytes(data))
        if response:
            p = await self.r.wait(EV_WRITE, 30)
            if status := p[2] | p[3] << 8:
                raise RelayError(f"write {char_uuid}: status 0x{status:x}")

    async def start_notify(self, char_uuid, callback):
        h = self.handle(char_uuid)
        cccd = next((a for a, t in self.attrs if a > h and (t == CCCD or t in DECLS)), None)
        if dict(self.attrs).get(cccd) != CCCD:
            raise RelayError(f"{char_uuid} has no CCCD")
        self.r.notify[h] = callback
        await self.r._send(WRITE, struct.pack("<HB", cccd, 1) + b"\x01\x00")
        p = await self.r.wait(EV_WRITE, 30)
        if status := p[2] | p[3] << 8:
            raise RelayError(f"subscribe {char_uuid}: status 0x{status:x}")

    async def pair(self, timeout=30):
        """Encrypted (paired or bonded before): (encrypted, bonded)"""
        if not (self.r.enc and self.r.enc[1]):
            await self.r._send(PAIR)
            async with asyncio.timeout(timeout):
                while not (self.r.enc and (self.r.enc[0] or self.r.enc[1])):
                    await asyncio.sleep(0.1)
        if self.r.enc[0]:
            raise RelayError(f"pairing: status 0x{self.r.enc[0]:x}")
        return bool(self.r.enc[1]), bool(self.r.enc[2])


async def main(a):
    async with Relay() as r:
        if a.cmd == "scan":
            for addr, (name, rssi) in sorted((await r.scan(a.secs, a.arg or "")).items(), key=lambda x: -x[1][1]):
                print(f"{rssi:4} dBm  {addr}  {name}")
            return
        dev = await r.find(lambda d, adv: adv.local_name == a.arg, a.secs, prefix=a.arg)
        if not dev:
            sys.exit(f"{a.arg} not heard in {a.secs} s")
        async with r.client(dev) as c:
            print(f"{a.arg} {dev.address}, MTU {c.mtu}")
            for h, u in c.characteristics():
                print(f"  0x{h:04x}  {u}")
            for name, u in [("model", 0x2A24), ("firmware", 0x2A26), ("hardware", 0x2A27)]:
                try:
                    print(f"{name}: {(await c.read_gatt_char(uuid16(u))).decode()}")
                except RelayError as e:
                    print(f"{name}: {e}")
            print("encrypted %s, bonded %s" % await c.pair())


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["scan", "info"])
    ap.add_argument("arg", nargs="?", help="scan: a name prefix; info: the board's name")
    ap.add_argument("--secs", type=float, default=10)
    a = ap.parse_args()
    if a.cmd == "info" and not a.arg:
        ap.error("info NAME")
    asyncio.run(main(a))
