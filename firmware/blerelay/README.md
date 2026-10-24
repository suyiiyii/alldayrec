# 蓝牙中继（ESP32-S3）

Mac 通过蓝牙连这块 ESP32，由 ESP32 当蓝牙主机去连被测板：扫描、连接、配对、读写特征、收通知。这样测试不占 Mac 的 USB 口，Mac 自己也不跟任何板子配对；Mac 锁屏时没法点配对确认，这一步交给 ESP32 在本地完成（LESC Just Works，和手机一样绑定，绑定信息存在 ESP32 的 NVS 里）。

硬件是 Seeed XIAO ESP32-S3，接上天线后插到充电头上就能用，不用接线。它广播的名字是 `relay-XXXX`（蓝牙地址末两字节），故意不用 `LR-` 开头，免得 App 把它当成录音设备。

```bash
# ESP-IDF v5.5.5，只装 ESP32-S3 的工具链（约 4 GB）
git clone --depth 1 --recursive --shallow-submodules -b v5.5.5 https://github.com/espressif/esp-idf.git ~/esp/esp-idf-v5.5.5
~/esp/esp-idf-v5.5.5/install.sh esp32s3
. ~/esp/esp-idf-v5.5.5/export.sh
idf.py -C firmware/blerelay build
idf.py -C firmware/blerelay -p PORT flash           # PORT：序列号是这块 ESP32 的 MAC 的那个口，见 uvx --from pyserial pyserial-ports -v
uv run firmware/blerelay/relay.py scan LR-          # 中继听到的设备
uv run firmware/blerelay/relay.py info <测试板的蓝牙名>   # 连上板子，列出特征，读设备信息，配对
```

- **密钥**：Mac 连上中继后要先过 HMAC-SHA256 挑战应答（每次连接一个新的随机数），不然中继什么都不做，免得旁边的人借它去连、去刷我们的板子。密钥是 64 位十六进制，放在仓库外的一个文件里（权限 600），路径由环境变量 `RELAY_KEY_FILE` 给出，编译和 `relay.py` 都要设。编译时把它写进构建目录里的头文件，不出现在命令行和日志里；`relay.py` 读同一个文件。换密钥就重新编译、重新刷。
- **脚本怎么改用中继**：`relay.py` 的 `Relay.find()` 和 `Relay.client()` 对应 bleak 的 `BleakScanner.find_device_by_filter()` 和 `BleakClient()`，`client()` 上有 `read_gatt_char`、`write_gatt_char`、`start_notify`，另外多一个 `pair()`。整个测试放在一个 `async with Relay() as r:` 里。
- **一次只连一块板**。Mac 断开时，中继会同时断开被测板：录音设备只接受一个连接，中继一直占着，手机就连不上了。
- **扫描只转发名字匹配前缀的广播包**（广播包或扫描响应，哪个里有名字就转发哪个）。校园里设备很多，全部转发会把 Mac 那条链路挤满。
- **和录音设备配对的副作用**：录音设备一旦有了绑定就开始录音（`capturing()`）。所以只拿专门的测试板配对，日常用的录音设备不要连。

协议在 `main/main.c` 开头：Mac 写 CMD 特征发命令，中继用 EVT 特征的通知回事件，每帧第一个字节是操作码，后面是小端序字段。

ESP-IDF 的构建暂时不进 CI（要多装一套工具链，这个中继只在测试台上用）。改了固件就在本机编译、刷入，再用 `relay.py scan` 确认。
