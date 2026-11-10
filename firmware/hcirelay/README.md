# USB 蓝牙中继（nRF52840 Pro Micro，HCI 控制器）

一块 Pro Micro（nice!nano 及其克隆，和 LR-P3 同一种板子）插在 Mac 的 USB 上，只当蓝牙控制器用：Mac 上的 [Bumble](https://github.com/google/bumble)（Python 写的蓝牙主机协议栈）经 USB 串口（H4）驱动它去扫描、连接、配对被测板。蓝牙主机在 Bumble 里，不在 macOS 里，所以：

- 不用 macOS 的蓝牙权限，也不弹配对确认；Mac 自己不和任何板子配对，系统蓝牙设置里看不到被测板。
- 绑定由 Bumble 做（LESC Just Works，和手机一样），密钥存在本机的一个 JSON 文件里，删掉它就是「忘记设备」。
- 不用写中继固件：固件就是 Zephyr 自带的 `samples/bluetooth/hci_uart`（原样编进来），加上 1200 bps 触碰进 UF2 bootloader（`src/touch.c`），以后重刷不用双击复位。

和 `firmware/blerelay`（ESP32-S3，Mac 经蓝牙连它）比：这块占一个 USB 口，但 Mac 那一侧没有蓝牙链路，也没有自定义协议，现成的 Bumble 工具和 API 都能直接用。

```bash
# 编译（NCS v2.7.0，和 lr.sh、selftest 一样），输出 build/zephyr/zephyr.uf2
cd /opt/nordic/ncs/v2.7.0
nrfutil sdk-manager toolchain launch --ncs-version v2.7.0 -- \
  west build -b xiao_ble/nrf52840 -d "$OLDPWD/build" "$OLDPWD" -- -DNCS_TOOLCHAIN_VERSION=NONE
# 刷入：板子在跑自检固件，或停在 UF2 盘时
uv run firmware/selftest/selftest.py flash SERIAL --uf2 firmware/hcirelay/build/zephyr/zephyr.uf2
# 之后重刷：stty -f PORT 1200 让它进 UF2 盘，再拷 UF2
```

板子以 `LR HCI relay` 枚举，串口就是 HCI。Bumble 的传输名写 `serial:/dev/cu.usbmodemXXXX`（波特率无所谓，USB CDC）：

```bash
uv venv .venv && uv pip install --python .venv bumble
.venv/bin/bumble-controller-info serial:/dev/cu.usbmodemXXXX   # Manufacturer: Nordic Semiconductor ASA
.venv/bin/bumble-scan serial:/dev/cu.usbmodemXXXX              # 附近的广播
```

脚本里：

```python
from bumble.device import Device, Peer
from bumble.keys import JsonKeyStore
from bumble.transport import open_transport

async with await open_transport("serial:/dev/cu.usbmodemXXXX") as (src, snk):
    dev = Device.with_hci("lr-relay", "F0:F1:F2:F3:F4:F5", src, snk)  # 固定的随机静态地址：绑定跟着它
    dev.keystore = JsonKeyStore(None, "keys.json")
    await dev.power_on()
    conn = await dev.connect(address)            # 先扫描，按蓝牙名核对地址
    await (conn.encrypt() if await dev.keystore.get(str(conn.peer_address)) else conn.pair())
    peer = Peer(conn)
    await peer.discover_services(); await peer.discover_characteristics()
    # peer.get_characteristics_by_uuid(UUID(...))[0].read_value() / write_value() / subscribe()
```

- **连之前先核对是哪块板**：中继听得到附近所有录音设备。被测板的蓝牙名 `LR-P3-XXXX` 里的 XXXX，自检固件的 `info` 会打印（`"ble"`），和 USB 序列号一一对应；连之前用扫描到的名字核对地址。
- **和录音设备配对的副作用**同 `firmware/blerelay`：有了绑定就开始录音，只拿专门的测试板配对。
- 低频时钟用校准过的 RC：这几批克隆板的 32.768 kHz 晶振偏到 +125 ppm，RC 在哪块板上都能用。
- 偶尔会在加密时断开（被测板那一侧），重连一次就好。
