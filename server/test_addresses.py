"""addresses.py: the 连接手机 page's phone-reachable addresses from `ip` / `ifconfig` output, tagged, filtered by the
bind address; and whether a request came through a proxy."""
from addresses import parse_ifconfig, parse_ip_addr, pick, proxied, tag

IP_ADDR = """1: lo    inet 127.0.0.1/8 scope host lo\\       valid_lft forever preferred_lft forever
2: eth0    inet 192.168.1.20/24 brd 192.168.1.255 scope global dynamic eth0\\       valid_lft 86000sec
3: docker0    inet 172.17.0.1/16 brd 172.17.255.255 scope global docker0\\       valid_lft forever
4: tailscale0    inet 100.100.1.2/32 scope global tailscale0\\       valid_lft forever
5: br-1a2b3c    inet 172.18.0.1/16 brd 172.18.255.255 scope global br-1a2b3c\\       valid_lft forever
"""

IFCONFIG = """lo0: flags=8049<UP,LOOPBACK,RUNNING,MULTICAST> mtu 16384
\tinet 127.0.0.1 netmask 0xff000000
en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
\tinet6 fe80::1%en0 prefixlen 64 secured scopeid 0xb
\tinet 10.0.0.7 netmask 0xffffff00 broadcast 10.0.0.255
bridge0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
\tinet 192.168.64.1 netmask 0xffffff00 broadcast 192.168.64.255
utun4: flags=8051<UP,POINTOPOINT,RUNNING,MULTICAST> mtu 1280
\tinet 100.80.0.5 --> 100.80.0.5 netmask 0xffffffff
utun9: flags=8051<UP,POINTOPOINT,RUNNING,MULTICAST> mtu 1500
\tinet 198.18.0.1 --> 198.18.0.1 netmask 0xfffffffc
en5: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
\tinet 1.2.3.4 netmask 0xffffff00 broadcast 1.2.3.255
"""


def test_parse_and_pick():
    assert parse_ip_addr(IP_ADDR) == [("lo", "127.0.0.1"), ("eth0", "192.168.1.20"), ("docker0", "172.17.0.1"),
                                      ("tailscale0", "100.100.1.2"), ("br-1a2b3c", "172.18.0.1")]
    assert pick(parse_ip_addr(IP_ADDR), "0.0.0.0") == [{"ip": "100.100.1.2", "kind": "tailscale"},
                                                       {"ip": "192.168.1.20", "kind": "lan"}], "no loopback, no Docker bridges"
    # macOS: bridge0 (VMs) and a proxy's fake-IP tun (198.18/15) aren't for phones; 1.2.3.4 stands for a public one
    assert pick(parse_ifconfig(IFCONFIG), "0.0.0.0") == [{"ip": "100.80.0.5", "kind": "tailscale"},
                                                         {"ip": "10.0.0.7", "kind": "lan"}, {"ip": "1.2.3.4", "kind": "public"}]


def test_bind():
    ifaces = parse_ip_addr(IP_ADDR)
    assert pick(ifaces, "127.0.0.1") == [], "listening on loopback only: no phone gets in"
    assert pick(ifaces, "100.100.1.2") == [{"ip": "100.100.1.2", "kind": "tailscale"}], "one bound address: only it"


def test_tag():
    assert [tag(x) for x in ("100.80.0.1", "100.127.255.254", "100.128.0.1", "172.16.0.1", "172.31.9.9", "172.32.0.1",
                             "169.254.1.1", "127.0.0.1", "8.8.8.8")] == \
        ["tailscale", "tailscale", "public", "lan", "lan", "public", None, None, "public"]


def test_proxied():
    assert not proxied("127.0.0.1:8770", False, 8770)
    assert not proxied("[::1]:8770", False, 8770)
    assert proxied("rec.example.com", False, 8770), "no port: 80 or 443 in front of the listener"
    assert proxied("192.168.1.20:8770", True, 8770), "X-Forwarded-* says so"
    assert proxied("192.168.1.20:9000", False, 8770), "a port forward (Docker's 9000:8770)"
