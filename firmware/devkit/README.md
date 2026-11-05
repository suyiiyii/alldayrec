# BLE 录音硬件固件（Omi devkit 改）

XIAO nRF52840 Sense 上的录音固件：PDM 麦克风 → Opus 16 kHz → 记录流：上电就一直写 SD 卡，手机连着时同一份记录（文件号 + 偏移）同时推过去，断开期间的部分手机回来后用 READ 补齐。
基于 [BasedHardware/omi](https://github.com/BasedHardware/omi) `omi/firmware/devkit` 的 2e878f6 版本（MIT，见 LICENSE），上游原样在本目录的第一个提交里，之后的提交是我们的改动。

## 型号与命名

| 型号 | 板子 | `lr.sh` 变体 | 电池读数 |
|---|---|---|---|
| `LR-X1` | XIAO nRF52840 Sense（板载麦克风，焊 SD 模块和电池） | `sdmod` | 板载分压 AIN7 |
| `LR-P3` | ProMicro nRF52840（nice!nano 兼容），飞线接 SD 模块、PDM 麦（SEL 接 3V）和拨动开关（P1.15），装进外壳；新机先跑 `selftest.py run SERIAL --p3` | `promicro-p3` | 芯片内部 VDDH/5（电池接 VDDH，不用外接电阻） |
| `LR-N4` | 自己的板子：裸 nRF52840（aQFN73），LR-P3 的全部件加第二颗麦和 IMU；新机先跑 `selftest.py run SERIAL --n4` | `n4` | AIN0（P0.02）经 1M/1M 分压 ×2；VDDH 接系统电源轨，插 USB 时是 USB 那一侧 |

`lr.sh` 的其余变体（`expansion`、`promicro`、`promicro-p2`）是维护者的测试板。

每台的蓝牙名是型号加身份地址末两字节，如 `LR-P3-XXXX`：地址跟着芯片走，刷固件不变。广播包放型号（`LR-P3`），扫描响应放完整名字；
设备信息服务里 Model 是型号，Hardware Revision 是板子。客户端都按记录流服务 UUID `7c3e0000-…`（广播里有）找设备。

## 构建与刷写（nRF Connect SDK v2.7.0，全命令行）

```bash
nrfutil install sdk-manager && nrfutil sdk-manager install --ncs-version v2.7.0   # 一次
./lr.sh build sdmod        # 焊在右排的 SD 模块（正式）
./lr.sh build expansion    # Seeed XIAO 扩展底板（只做测试）
./lr.sh build promicro-p3  # LR-P3：ProMicro nRF52840（nice!nano 兼容）+ PDM 麦克风 + SD 模块 + 拨动开关
./lr.sh build n4           # LR-N4：自己的板子（引脚见下面「LR-N4」）
./lr.sh flash sdmod        # 运行中的固件收到 1200 bps 触碰自己进 UF2 bootloader，不用按 reset
```

固件第一次刷进（或卡死）时才需要双击 reset 进 bootloader。

## 发布到 App 在线升级

```bash
# 1. 起草：列出这个型号上次发布以来改了固件的提交（不构建、不上传）
LR_TOKEN=<服务器 AUTH_TOKEN> uv run --no-project python lr_release.py promicro-p3 --server https://live.example.com
# 2. 把草稿改写成给用户看的说明，再发布
LR_TOKEN=<服务器 AUTH_TOKEN> LR_FIRMWARE_TOKEN=<服务器 FIRMWARE_TOKEN> \
  uv run --no-project python lr_release.py promicro-p3 --server https://live.example.com --notes "$(cat notes.txt)"
```

逐个变体构建、打包、上传；型号取自那次构建自己的 `.config`（`CONFIG_BT_DIS_MODEL`）：sdmod→LR-X1、promicro-p3→LR-P3，
其余变体对应维护者测试板的型号。只发布已在 origin/main 上、本目录无未提交改动的提交。
App 录音设备页 › 固件更新列出这个型号的所有版本和说明（新的在上，标出当前和最新），用户点了才升级。

说明（`--notes`）写给用户看：中文、短、一行一条，讲用户能感觉到的变化，比如「开关要拨稳 0.5 秒才算数」「灯的含义改了：……」；
不写 issue 号、引脚、芯片、驱动和工具名、内部代号和真名（服务器拒收带 `#数字` 的说明）。一起发布的型号共用 `--notes`，
上次发布的版本不同就分开跑。已发布版本的说明可以改：`--version <版本> --notes …`（不构建，包不变）。
固件版本号（DIS Firmware Revision）是 `YYYYMMDD.HHMMSS-<短哈希>`，取提交时间，按文本比较即新旧；本目录有未提交改动时末尾加 `+`，这种构建服务器不收。

## 接线（sdmod）

模块（3.3V CS MOSI CLK MISO GND）沿 XIAO 右排对齐：3V3–3.3V、D10–CS、D9–MOSI、D8–CLK、D7–MISO，GND 飞线回右排 GND。
左排 D0–D6 留空：D4/D5 是排针上仅有的非「low frequency I/O」引脚，留给 I2C（RTC）；D6 留给 UART 日志。见 `overlay/lr_sdmod.overlay`。

## 接线（LR-P3）

下面按 ProMicro 丝印列出每根飞线，摘自 [`hardware/lr-p3/README.md`](../../hardware/lr-p3/README.md) 的引脚表；孔位、线色、下料长度和焊接顺序以那里为准。
- 麦克风（3V GND SEL CLK DAT）：006（P0.06）–DAT、008（P0.08）–CLK、017（P0.17，GPIO 供电）–3V、左列 GND–GND；SEL 用跳线接麦板自己的 3V。
- SD 模块（3V3 CS MOSI CLK MISO GND）：020（P0.20）–CS、022（P0.22）–MOSI、024（P0.24）–CLK、100（P1.00）–MISO（满速引脚），右列 VCC（P0.13 控制通电）–3V3，左列 GND–GND。
- 开关：115（P1.15，内部上拉）–中脚，右列 GND–边脚。
- 电池：红线–B+，黑线–右列 GND。

RST 不要接地。bootloader 是 Adafruit UF2 + S140 6.1.1，应用从 0x26000 起（`overlay/lr_promicro.overlay`）。

## LR-N4

`n4` 变体在 LR-P3 的基础上（`lr_promicro.overlay`：同一套 bootloader 和分区、同样的麦 DAT/CLK、SD 四根线和开关）叠 `overlay/lr_n4.overlay`，功能和 LR-P3 一样（录音、卡、蓝牙、DFU、电量、充电状态、开关、灯、手机模式）。不同的地方：

| 功能 | 脚 | 说明 |
|---|---|---|
| 麦克风 LDO 使能（MIC_PWR） | P0.12 | 两颗麦共用一颗 3.0 V LDO；LR-P3 是 P0.17 直接供电 |
| TF 卡 LDO 使能（SD_EN） | P0.31 | LR-P3 是 P0.13 |
| 第二颗麦 MK2 | 同一对 CLK/DAT | MK1 L/R 接电源、MK2 接地；固件仍是单声道下降沿，只录 MK1。怎么录 MK2 或两颗都录，见 `lr_n4.overlay` 的注释 |
| 灯 | 黄绿 P0.15、红 P1.13 | 状态闪码照旧在 P0.15（现在是黄绿）；红灯只在开机启动麦克风那一下和启动出错时亮 |
| IMU（LSM6DS3TR-C，0x6A） | SDA P0.23、SCL P0.19、INT1 P0.11、供电 P1.08 | I2C 首批板用 100 kHz（nRF52840 勘误 219）；开机读 WHO_AM_I 后加速度、陀螺都停在掉电模式，现在不用它 |
| 电池电压 | AIN0（P0.02）×2 | 1M/1M 分压，采样 40 µs |
| 充电状态（TP4054 CHRG） | P0.29 | 只在有 VBUS 时、只在读的那一下开片内上拉：充电中读低、充满读高 |
| 32.768 kHz 晶振 | XL1/XL2 | X1 Seiko SC-16S（Q-SC16S0322070AAAN）：规格书 WF11077297 写 25 °C ±20 ppm、二次温度系数 −0.036 ppm/°C²（拐点 25 ± 5 °C，−40 °C 时 −194 ppm）、每年 ±3 ppm 老化、两次回流后 ±10 ppm。最坏情况约 240 ppm，所以声明 `K32SRC_250PPM`，和 ProMicro 一样；自检在室温下按 ±50 判 |

供电：VDDH 接系统电源轨，芯片工作在高压模式，REG0、REG1 两级 DCDC 的电感都焊了，固件照 LR-P3 打开两级 DCDC。VDD = 3.0 V 由 UICR REGOUT0 = 4 决定，是第一次用 SWD 烧 bootloader 时一起写进去的，固件不写 UICR。nice!nano 0.10.0 bootloader 发现 REGOUT0 还是擦除值时会自己写成 3.3 V，所以 UICR 文件必须和 bootloader 放在同一条 load 里。
APPROTECT：`n4` 显式打开 `CONFIG_NRF_APPROTECT_USE_UICR`（NCS 2.7 的默认），build code F 及以后的芯片，UICR APPROTECT 写成 0x5A 时，SystemInit 每次启动写 APPROTECT.DISABLE，调试口保持打开；不开 `CONFIG_NRF_APPROTECT_LOCK`。
新板没有 bootloader：
1. 第一次用 SWD 只烧 bootloader + UICR。
2. 插 USB，板子停在 `NICENANO` 盘：bootloader 设置页（0xFF000）是空的，它不会运行用 SWD 烧进去的应用，拷一次 UF2 才会写设置页。
3. 先拷自检固件（`firmware/selftest`，`selftest.py flash SERIAL --n4`），之后和 LR-P3 一样走 UF2 / 蓝牙 DFU。

## 低电关机

用电池时每分钟读一次电池电压（LR-X1 是板载分压，LR-P3 是芯片内部的 VDDH）。连续 3 次低于 `CONFIG_LR_LOW_BATTERY_MV`（默认 3300 mV，0 = 不关）时，固件依次：记一条 POWER（a=2），写完并关闭正在录的文件，断开手机，重启。重启后在挂卡、开蓝牙之前直接进入 System OFF。
唤醒方式：插 USB；LR-P3 也可以拨一下开关（关→开）。插着 USB 时不做判断（读到的是线上的电压）。
阈值依据：X1 三次放电，读数到 3.30 V 后还能录 25–29 分钟；读数到 2.98–3.08 V 时，离断电只剩 5–9 分钟，之后反复欠压重启。

## 记录流

协议和文件格式见 `src/lr_store.h`。要点：
- 每 10 帧（100 ms）封一条 `'A'` 记录；封好的记录立即通过 LIVE 通知推给订阅的手机（`file offset bytes`），同时进 RAM 批缓冲，攒到 `CONFIG_LR_STORE_BATCH`（4 KB，约 1 s）成一批进 RAM 环。环里剩的空间不够再放 3 批（`3 × (BATCH + 512)`）或距上次写卡满 10 s 时，整环写卡（`card_due`）：16 KB 环每批都写，和以前一样；LR-P3 的 44 KB 环约第 8 批写一次，约 8 s。原因：卡每写一次要以约 27 mA 醒 1.8–2.0 s，每秒写一次它就一直醒着（LR-P3 卡模式 27.3 mA；实测每 5.5 s 写一次 13.4 mA，每 11 s 10.4 mA）。按约 8 s 一次推断 LR-P3 卡模式约 11 mA、续航约 27 h，待实测。代价：LR-P3 突然断电最多丢约 10 s（环里约 8 批加正在攒的一批；以前约 1 s）；看门狗复位、崩溃时环留在 RAM 里不丢（见下），正在攒的那一批在 .bss 里，最多丢约 1 s；关机、低电、升级前先全部写卡。写卡出错时，排在后面的批次另起一个新段，卡恢复后写上。
- 手机写 TIME 时，固件往流里插一条 `'T'`（帧号 = 手机时间）；手机是时钟的权威源，按同一次开机里最近的 `'T'` 算每块的开始时间。
- 连接不再开新段：段只在开机、16 MB、卡写不进（无卡 / 卡满 / 借给 USB / 写失败）时切换。LIST 列出正在写的段，READ 读它时先把已封好的记录写卡再读。
- 卡上的文件永远是流里同名文件的前缀：卡出问题时流照常推，卡恢复后从新段继续。
- 手机模式（LR-P3，`CONFIG_LR_STORE_PHONE`）：App 跟着 LIVE、每 5 s 回一次 ACK（「这个文件我已存到哪」）时，卡停写，空闲 10 s 后断电（`CONFIG_LR_STORE_CARD_OFF`，`LR_CARD_ON=1 ./lr.sh build promicro-p3` 编出卡不断电的版本做对比）。没确认的批次留在 RAM 环（44 KB，约 11 s）里，READ 落在环里的直接从 RAM 回答。断连、取消订阅、8 s 没 ACK、环里没确认的超过 3/4（约 9 s）、重启或借卡时，没确认的批次接成一个新段写上卡，流在新段里继续；其中手机可能已经收到的那几秒，由服务端按帧去重。环放在 `__noinit`：看门狗复位或崩溃后，下次开机先把它写成上一次开机的下一个段。不发 ACK 的 App（老版本、`lr_listen.py`）下行为和以前一样。TIME 读取偏移 48 的字节 bit 0 表示固件收 ACK。

参考客户端 `lr_sync.py`（对时、列文件含当前段、读取、可选跟读 LIVE 并和卡上逐字节比对、按 `'T'` 还原墙钟时间、导出 WAV、可选删除）：

```bash
uv run --no-project --with bleak python lr_sync.py OUT_DIR [--follow 60] [--delete]
uv run --no-project --with bleak python lr_sync.py --selftest
# 记录流的主机端检查（LIVE 与卡上字节一致、批写、'T'、丢帧、写卡失败），命令见文件头
cc ... test/store_check.c
```

在 Mac 上实时听一台录音设备：`lr_listen.py` 只连 `--name` 指定的那一台（蓝牙名完全一致，且广播可连接，也就是没有手机连着），订阅 LIVE，边解码边播放，延迟约 0.4 s。每秒打印一行：RMS、50 Hz 哼声（50–250 Hz 谐波合计）、≥10 ms 全 0 的毫秒数、满幅样本数、链路丢的毫秒数、从麦克风到喇叭的延迟。只写 TIME，不发 CTRL 命令：

```bash
uv run --no-project --with bleak --with sounddevice python lr_listen.py --name LR-P3-XXXX [--seconds 60]
uv run --no-project --with bleak python lr_listen.py --selftest
```

## 已知问题

- SD 卡的 SPI 协议用 `src/sdhc_spi.c`：NCS 2.7 驱动的副本，改了三处。数据命令的 R1 晚于第 2 个字节时，原来要睡 10 ms；每次多块读后 CMD12 的忙超出缓冲，原来要睡 30 ms；现在都改成先连续轮询。写卡后卡一直忙，原来会永远轮询，现在每次 10 s 超时报错。主机检查：`test/sdhc_check.c`
- SD 总线 8 MHz：ProMicro（LR-P3）用 SPIM（EasyDMA）；XIAO（LR-X1）仍用 legacy SPI（`nordic,nrf-spi`，每字节一次中断）。10-03 SPIM 在 XIAO 焊好的模块上初始化挂死，当时还没有挂载前的 SD 复位；它的 SD 接在 D7–D10，是 low frequency I/O 引脚。之后没有在 XIAO 上复测
- NCS 2.7 `card_ioctl()` 在 `DISK_IOCTL_CTRL_SYNC` 后缺 `break`，FatFs 每次 sync 都报 -EIO（数据其实写进去了）：新卡第一次开机 `mkdir LR` 因此报错、一分钟不写卡。`sdcard.c` 用链接器 `--wrap=sdmmc_ioctl` 把这个假错改成 0，升级到修好的 NCS 后删掉
- 卡写失败或开机时没卡：流照常推（TIME 的 store 显示错误），每分钟重新挂载一次卡，成功后从新段继续写（开机时没卡的这次才计开机次数）
- 写卡在自己的线程（`card_thread`）里做，切记录和 LIVE 不等卡：卡卡住时，记录照常发给手机。写卡队列（16 KB 能放 3 批，LR-P3 44 KB 能放 10 批）在写卡开始后还能再收约 3 s；卡卡住超过这么久，放不下的批次只是不上卡，记一条 APPEND_ERR（x 1，−EAGAIN），这一段后面的也不再上卡；写卡线程追上后从新段继续。所以卡上每个文件仍是流里同名文件的开头
- `'T'` 的帧号取的是对时那一刻正在编码的帧，这一帧的声音比它早约 0–100 ms（麦克风按 100 ms 一块交数据），时间系统性偏晚约 50 ms
- 热复位时 SD 卡可能卡在命令中间只能断电恢复；1200 bps 复位前会先卸载，看门狗复位仍有风险
- 开机前约 0.3 s 音频丢失；`MIC_GAIN` 偏高会削顶；BLE 连接间隔（~20 Hz）串进麦克风的「哒哒」声未修
