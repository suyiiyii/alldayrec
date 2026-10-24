"""GET /api/addresses: where a phone can reach this server, for the 连接手机 page's address picker. The page's own
origin is often 127.0.0.1 (opened on the server's machine), which a phone can't use; these are the machine's other
IPv4 addresses, tagged tailscale / lan / public, with the port the server listens on.
In a container the interfaces are the container's own (a Docker bridge address no phone reaches): none, and
in_container tells the page to ask for the address by hand."""
import ipaddress
import os
import re
import shutil
import subprocess

from fastapi import APIRouter, Request

router = APIRouter()
TAILSCALE = ipaddress.ip_network("100.64.0.0/10")  # CGNAT space, Tailscale's
LAN = tuple(ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
# ponytail: virtual bridges by name (Docker, libvirt, macOS's bridge0); a host with other virtual nets lists them too
VIRTUAL = ("docker", "br-", "veth", "virbr", "bridge")


def parse_ip_addr(text: str) -> list[tuple[str, str]]:
    """`ip -o -4 addr` (Linux): (interface, address) per line."""
    return re.findall(r"^\d+:\s+(\S+)\s+inet\s+([\d.]+)/", text, re.M)


def parse_ifconfig(text: str) -> list[tuple[str, str]]:
    """`ifconfig` (macOS, BSD, Linux net-tools): (interface, address) for each inet line under its header."""
    out, name = [], ""
    for line in text.splitlines():
        if m := re.match(r"(\S+?):? flags=", line):
            name = m[1]
        elif m := re.match(r"\s+inet (?:addr:)?([\d.]+)", line):
            out.append((name, m[1]))
    return out


def tag(ip: str) -> str | None:
    """tailscale, lan, public; None for what no phone reaches (loopback, link-local, a proxy's fake-IP range...)."""
    a = ipaddress.ip_address(ip)
    if a in TAILSCALE:
        return "tailscale"
    if any(a in n for n in LAN):
        return "lan"
    return "public" if a.is_global else None


def pick(ifaces: list[tuple[str, str]], bind: str) -> list[dict]:
    """The addresses a phone may use: the real interfaces' (not loopback or a virtual bridge), and only the bound one
    when the server listens on one address (127.0.0.1: none)."""
    seen = []
    for name, ip in ifaces:
        if name.startswith(VIRTUAL) or (bind not in ("0.0.0.0", "::", "") and ip != bind):
            continue
        if (k := tag(ip)) and ip not in [x["ip"] for x in seen]:
            seen.append({"ip": ip, "kind": k})
    order = ("tailscale", "lan", "public")
    return sorted(seen, key=lambda x: order.index(x["kind"]))


def proxied(host: str, forwarded: bool, port: int) -> bool:
    """The request came through a proxy: it says so (X-Forwarded-*), or its Host port isn't the listener's (no port:
    80, the server itself has no TLS)."""
    m = re.search(r":(\d+)$", host)
    return forwarded or (int(m[1]) if m else 80) != port


def interfaces() -> list[tuple[str, str]]:
    cmd, parse = (["ip", "-o", "-4", "addr"], parse_ip_addr) if shutil.which("ip") else (["ifconfig"], parse_ifconfig)
    try:
        return parse(subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout)
    except (OSError, subprocess.SubprocessError):
        return []


def in_container() -> bool:
    return os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv")


@router.get("/api/addresses")
def addresses(request: Request):  # sync: the subprocess runs in FastAPI's thread pool
    port = request.scope["server"][1]
    box = in_container()
    h = request.headers
    return {
        "addresses": [] if box else pick(interfaces(), os.environ.get("HOST", "127.0.0.1")),
        "port": port,
        "bind": os.environ.get("HOST", "127.0.0.1"),
        "in_container": box,
        "proxied": proxied(h.get("host", ""), "x-forwarded-proto" in h or "x-forwarded-host" in h, port),
    }
