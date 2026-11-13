# 板子自检固件（nRF52840 Pro Micro：nice!nano 及其克隆）

新到的板子先刷这个，把板上能测的元件都过一遍，再刷我们的固件。它和 LR-P3 用同一套 bootloader 和分区（Adafruit UF2 bootloader + S140 6.1.1，应用从 0x26000 开始），通过 UF2 刷入，不需要 SWD。

```bash
python3 firmware/selftest/selftest.py build                   # 编译，NCS v2.7.0，和 lr.sh 一样
uv run firmware/selftest/selftest.py flash SERIAL             # 只刷这一块板
uv run firmware/selftest/selftest.py run SERIAL               # 跑全部检查，打印 PASS/FAIL 报告；有 FAIL 时退出码为 1
python3 firmware/selftest/selftest.py check                   # 不接板子：用录下的好、坏麦克风数据核对麦克风两项的判定
```

- **板子用 USB 序列号指定**：就是芯片的 DEVICEID，bootloader 和自检固件里都一样，`ioreg -p IOUSB -l -w0` 能看到。
- **`flash` 不会做 1200 baud 触碰**：板子停在 bootloader 时，按序列号找到它的 UF2 盘直接拷；板子在跑自检固件时，先发 `dfu` 命令让它进 bootloader。都不满足（比如板子在跑别的固件）就退出，提示双击 RST。拷之前会当场再核对一次序列号，所以别的板子同时插着也不会刷错。
- **第一次插上要允许**：新固件第一次出现在 Mac 上时，要在「允许配件连接」弹窗里点「允许」，否则 USB 设备停在 `!registered`，也没有串口。
- **LR-P3 整机**（板子已经飞线接了 SD 模块、PDM 麦和开关）：`uv run firmware/selftest/selftest.py run SERIAL --p3`。GPIO 测试跳过接了外设的 8 个脚（不驱动它们，免得和 SD 卡 MISO、麦克风 DAT 对顶），另加 SD 卡、麦克风、开关三项；跑到开关那步会提示在 20 秒内来回拨一下（`--flip 0` 不等）。这种板子上不要在串口里敲 `gpio` 或 `all`，要用 `gpio p3`。
- **不用脚本也能测**：用任何串口终端打开板子的串口，输入 `all` 也能跑板子自己能判断的那几项，每项回一行 `R {json}`，其中 `ok` 就是结论。

## 测什么、怎么判

| 项目 | 怎么测 | 通过标准 |
|---|---|---|
| 芯片、UICR | 读 FICR（型号、变体、容量、DEVICEID，以及 DEVICEADDR 的低 16 位：刷上录音固件后蓝牙名 `LR-P3-XXXX` 的 XXXX，用来给板子编号）和 UICR（REGOUT0、PSELRESET、APPROTECT、NFCPINS） | 只记录 |
| USB | 板子以 `LR selftest` 枚举，串口能收发 | 串口连得上、能应答 |
| 32 MHz 晶振起振 | 内核启动前，重启 HFXO 并计时 | 5 ms 内起振（规格书远小于 1 ms） |
| 32.768 kHz 晶振起振 | 内核启动前，把低频时钟切到晶振并计时；之后内核和射频都改用 RC，晶振坏了也不会卡死 | 1 s 内起振（典型 0.25 s） |
| 两颗晶振的相对频差 | 晶振上数 65536 个 RTC 节拍（2 s），用 CPU 周期计时；两颗都准时正好是 1.28 亿个周期 | 只记录，用来算下面的绝对精度 |
| 32 MHz 晶振精度 | 板子用 TIMER4 以 16 MHz 计数，每秒报 20 次，共 60 s；主机拿这些计数和 Mac 的时钟（NTP 校准）做线性拟合 | ±40 ppm（nRF52840 跑 BLE 的要求） |
| 32.768 kHz 晶振精度 | 相对频差加上 32 MHz 晶振的绝对频偏 | ±250 ppm：ProMicro 固件（`lr.sh` 的 promicro 系列）设了 `CONFIG_CLOCK_CONTROL_NRF_K32SRC_250PPM`。它做蓝牙外设时，按「手机声明的误差 + 本机 250 ppm」放宽每次连接的接收窗口（这几批克隆板实测 +47–125 ppm，有的在 +104 和 +125 之间跳）。超过 250 判 FAIL：这块板会漏连接事件、和手机断开，要换 32.768 kHz 晶振。Mac 时钟被 NTP 调速时，绝对值会有约 ±10 ppm 的误差，「32.768 kHz vs 32 MHz」那一行更可靠 |
| 射频发射 | 板子一直发不可连接广播，名字是 `LRT-<序列号>`；Mac 扫描 8 s | Mac 能收到，RSSI ≥ −80 dBm（板子离 Mac 1 m 以内） |
| 射频接收 | 板子被动扫描 5 s | 至少收到一台设备，最强 RSSI ≥ −90 dBm（附近总有手机或电脑） |
| GPIO 焊盘 | Pro Micro 封装上的 21 个 GPIO（两排排针，加上底边的 P1.01、P1.02、P1.07）：<br>① 每个脚单独上拉应读 1、下拉应读 0，否则就是连到了 GND 或 VDD；<br>② 每个脚依次输出低（其余上拉）、输出高（其余下拉），跟着变的脚和它短路（焊锡桥） | 没有卡死的脚，没有短路 |
| VDD | SAADC 量芯片稳压器的输出 | UICR REGOUT0 设定值 ±5%（REGOUT0 擦除时是 1.8 V） |
| VDDH（电池输入） | SAADC 量 VDDH/5。USB 和电池都从这里进来；插着 USB 时量到的是 USB 那一侧（经过板上的二极管） | 插 USB 时 4.3–5.5 V |
| P0.04 | nice!nano v2 的电池分压脚；克隆板上可能没有分压电阻 | 只记录 |
| 充电状态脚 | 这个封装上没有：充电灯直接接在充电芯片上（XIAO 是 P0.17） | 不判 |
| 蓝色 LED（P0.15） | 闪 5 次 | 人眼看 |
| DCDC（电感） | 依次打开 REG1、REG0 两路 DCDC，每次量 VDD。缺电感时芯片会掉电复位：主机看到串口消失，就报是哪一路 | 都不复位，VDD 都在范围内 |

LR-P3 整机另加（`--p3`，引脚同 `lr_promicro.overlay`）：

| 项目 | 怎么测 | 通过标准 |
|---|---|---|
| SD 卡 | P0.13 拉低断 VCC 300 ms 再上电；250 kHz 下 CMD0/CMD8/ACMD41/CMD58 进 SPI 模式、CMD9 读容量；切到固件用的 8 MHz：最后一个扇区写入图案、读回比对、再写回原内容（卡上文件不变）；再读前 1 MB，数失败块和 CRC 错（线上丢位） | 启动、读写校验都过，写回成功，1 MB 读 0 失败。分区类型只记录，但录音固件只认 FAT32（0x0B/0x0C）或 exFAT（0x07） |
| 麦克风 | 按固件的配置（P0.17 供电，1.28 MHz，单声道下降沿采样，比率 80，增益 0x28）先安静采 2 s，再在 Mac 用 `say` 念数字时采 3 s | 安静时不是一条直线（直线 = DAT 卡死：没时钟、没供电，或 DAT 断/短）；念数字时最响的 100 ms 比安静时高 10 dB 以上（达不到报 LOOK：Mac 离得远或静音，对着板子说话重跑）。安静那项没过就不判说话、只报 LOOK：麦克风没电或 DAT 卡住时，每次上电 PDM 输出仍有一段衰减到直线的瞬态，最响一块会比直线高出 30 dB 以上 |
| 开关（P1.15） | 只开内部上拉读电平，不驱动；合上接 GND 为开 | 等待期间读到开、关两档；只读到一档报 LOOK（断线也读成「关」） |

## LR-N4

LR-N4（自己的板子，裸 nRF52840）用单独的构建，引脚和 `firmware/devkit/overlay/lr_n4.overlay` 一样，不能和 Pro Micro 的混刷：

```bash
python3 firmware/selftest/selftest.py build --n4            # 输出在 firmware/selftest/build-n4/zephyr/zephyr.uf2
python3 firmware/selftest/selftest.py swd                    # SWD 首烧（bootloader + UICR）后、Pico 探针还接着：读回 FICR / UICR，打印 USB 序列号
uv run firmware/selftest/selftest.py flash SERIAL --n4       # 拆光探针线、插 USB：板子停在 NICENANO 盘，拷自检固件
uv run firmware/selftest/selftest.py run SERIAL --n4 [--flip 20] [--cover 5]
```

首烧顺序：
1. SWD 只烧 bootloader 和 UICR。用 SWD 烧进去的应用不会运行：bootloader 设置页（0xFF000）是空的，它会停在 `NICENANO` 盘，拷一次 UF2 才写设置页。
2. `swd` 读回。
3. 拆光探针的线，插 USB。
4. `flash SERIAL --n4`。
5. `run SERIAL --n4`。

UICR REGOUT0 不是 4（3.0 V）时（比如 UICR 漏烧，bootloader 会自己写成 3.3 V），`run --n4` 判完芯片那一行就退出，固件也拒绝 `gpio`、`sd`、`vcc`、`mic`、`imu`、`all`，不给 3.0 V 的卡、麦、IMU 上电。

`run --n4` 先读 `info` 核对板子上跑的是 N4 版（不是就退出，免得 Pro Micro 版的 `gpio` 去驱动 N4 的外设脚）。在上面各项之外（射频、晶振、DCDC 照旧；VDD 按 3.0 V 判 2.70–3.15 V；32.768 kHz 按室温 ±50 ppm 判：固件声明 250 ppm，N4 的晶振（Seiko SC-16S）在 −40 °C 会再偏 −194 ppm，室温超过 50 时低温端就出了 250）：

| 项目 | 怎么测 | 通过标准 |
|---|---|---|
| 芯片、UICR | `info` 的 REGOUT0、PSELRESET，按 build code 判 APPROTECT：F 及以后要 UICR APPROTECT = 0x5A，且固件启动后 APPROTECT.DISABLE（0x40000558）= 0x5A | REGOUT0 低 3 位 = 4（3.0 V）、PSELRESET = 0x12，APPROTECT 按左边 |
| GPIO | ① 板上没接东西的 28 个 GPIO 球，照 Pro Micro 的做法查卡死和短路；② 打开卡、麦、IMU 三路电源后，外设上拉的 5 根线（SD CS、MOSI、MISO，IMU SDA、SCL）各自应读高，再逐根拉低，看有没有别的线（这 5 根、空闲球、SD SCK、麦 CLK / DAT）跟着变低。接外设的脚只在外设上电后才拉，只往低拉，不往高推 | 没有卡死、没有短路 |
| 电池、VDDH | AIN0 ×2；VDDH 是系统电源轨（插 USB 时经二极管来自 USB） | 电池只记录；VDDH 4.3–5.5 V |
| 充电状态（CHRG P0.29） | 有 VBUS 时开片内上拉读一下就关 | 电池低于 4.1 V 时必须读到「充电中」（低）；满电或没接电池不判 |
| SD 卡 | 同 LR-P3，电源换成 SD_EN（P0.31）控制的 LDO | 同 LR-P3 |
| 两颗麦 | `mic N s` 立体声：左 = MK1（下降沿）、右 = MK2（上升沿）；每颗各自判安静底噪和说话（同 LR-P3）；再依次提示「捂住 MK1 / MK2 的声孔」，Mac 念数字时录 3 s | 每颗不是直线、说话比安静高 10 dB；捂住的那颗相对另一颗下降 ≥ 6 dB |
| IMU | `imu`：P1.08 上电，100 kHz 读 WHO_AM_I；加速度、陀螺 104 Hz，数据就绪接到 INT1，等 INT1 变高 10 次、每次读一组；读完关两路传感器、关 TWIM、放开三根线、P1.08 拉低 | WHO_AM_I = 0x6A，INT1 10/10，平放 \|a\| 800–1200 mg，陀螺 < 25 dps |
| 开关 | 同 LR-P3（P1.15） | 同 LR-P3 |
| 两颗灯 | P0.15 黄绿、P1.13 红轮流各闪 5 次 | 人眼看 |

`vcc N` 在 N4 上同时打开 SD_EN 和 MIC_PWR，用来量 SD_VDD、MIC_VDD。

`swd` 用 pyOCD（`-O auto_unlock=false`，只读）读 FICR INFO.PART / VARIANT、UICR PSELRESET[0]/[1]、REGOUT0、NRFFW[0]/[1]、APPROTECT、APPROTECT.DISABLE、CTRL-AP 状态和 CPU 状态，按 build code 判。

测不到的：
- **焊盘虚接**（芯片引脚和焊盘没连上）：这要在焊盘上接线才能测出来。
- **复位键**：要人按。
- **充电电流**：要接电池。
- **VCC 开关**（P0.13 控制的 VCC 排针）：要在 VCC 排针上量电压。
- **LR-N4**：两颗 LDO 的输出电压、SD_VDD 断电后是否真的掉到 0.2 V 以下（`vcc N` 打开后用表量）；两颗麦的频响对比（要扫频声源）；I2C 波形（要示波器）；外设线之间、外设线和电源域之间在断电状态下的桥连（上电前用万用表查）。

## LR-N5

LR-N5 是 LR-N4 加一颗裸 SPI NAND（W25N01GV，或同脚位的 W25N02KV），用 SD 卡那四根线（/CS P0.20、DI P0.22、CLK P0.24、DO P1.00）。和 N4 不同的脚：NAND 的 LDO 使能 **MEM_EN P0.29**、充电状态 **CHRG P0.31**（布线时对调了），UART 调试脚 P0.13（TX）、P0.17（RX）只接调试口。

### SWD：首烧和回流后的快速检查（`swd.py`）

Pico 跑 debugprobe（或 lr-probe，带三路 ADC），**电池、板子的 USB 都不接，Pico 的 3V3 接 VDD**。pyOCD 固定 0.45.1，装在脚本自己的 uv 环境里；探针用 `-u` 指定（`pyocd list` 里的序列号）。

```bash
uv run firmware/selftest/swd.py check -u PROBE                                   # 不停核、不复位、不写 flash / UICR / 外设：芯片、锁没锁、UICR 现状、下一步
uv run firmware/selftest/swd.py flash -u PROBE --bootloader nice_nano_bootloader-0.10.0_s140_6.1.1.hex [--unlock]
uv run firmware/selftest/swd.py diag -u PROBE [--usb yes|no] [--adc auto]        # 回流后、焊 B 面前
uv run firmware/selftest/swd.py uicr [--fxx] -o uicr-n5.hex                      # 只生成 UICR 文件
uv run firmware/selftest/swd.py test                                             # 离线自测
```

- **所有命令（包括 `check`）一连上就会写内核的调试寄存器**：pyOCD 置 DHCSR.C_DEBUGEN、DEMCR.TRCENA，打开 DWT 计数器和 FPB。因为 `resume_on_disconnect=false`，断开时不清，芯片一直留在调试状态（电流偏大），**直到断电**。要量待机电流，先拔 Pico 断一次电。所有 pyocd 调用都带 `--no-config -O chip_erase=sector`：工作目录里的 `pyocd.yaml` 改不了擦除方式，mass erase 只在 `--unlock` 时发生。
- **`check`** 只用 attach 连接：不停核、不复位、不写 flash、UICR 和外设寄存器（调试寄存器见上一条）。锁着的芯片（build code F 及以后出厂就锁）只报告，不解锁。**已经 `flash` 过的 F 片，断过电后也报 locked**：它停在 bootloader 里，bootloader 不开 SWD，这是正常的，**不要再走 `--unlock`**（会擦掉 bootloader 和 UICR），第一个应用走 UF2。
- **`flash`**：先核 bootloader 的 sha256（nice!nano 0.10.0，`9b6bd9fd…0d8e`）；`--connect halt` 读 INFO.VARIANT 定 build code；生成 N5 的 UICR 文件；**bootloader 和 UICR 一条 `pyocd load`**（`--no-reset -O load.pre_reset=off`：芯片从头到尾不运行、不复位）；读回 UICR 各字、NRFFW、CTRL-AP、CPU 状态，再用 `savemem` 把两个 HEX 定义的每个字节读回来比对。全部 PASS 才打印「现在拔掉 Pico 的全部线，再接电池」。**load 之前先读 MAINREGSTATUS 和 USBREGSTATUS**：芯片看到 VDDH（电池，或焊了 D2 后的 USB）或 VBUS（板子的 USB）就拒绝烧录，要求拔掉再来。M11 = A 是「电池、USB 都不接，Pico 3V3 接 VDD」；VDDH 有电时 REG0 和 Pico 同时驱动 VDD（REGOUT0 还没写时 REG0 出 1.8 V），选拒绝而不是警告，是因为警告会被刷屏淹掉，而拒绝的代价只是拔根线重跑。芯片锁着时读不到这两个寄存器，mass erase 之后、load 之前再查。这时芯片里只有 bootloader，第一个应用走 UF2（下面「自检固件」）；B 面的 Q1、D3 没焊时，接电池不一定能上电。芯片锁着时不加 `--unlock` 就停；加了才做一次 mass erase（擦 flash、UICR、RAM），**只用于新片**。
- **N5 的 UICR**（nRF52840 PS v1.11，地址和位域已对 Nordic 的 SVD 核过）：

  | 地址 | 字 | 意思 |
  |---|---|---|
  | `0x10001200` / `0x10001204` | `0x00000012` | PSELRESET[0] / [1]：P0.18 是复位脚（两个都要写） |
  | `0x10001208` | `0x0000005A`（只 build code F 及以后） | APPROTECT = HwDisabled：应用每次启动写 APPROTECT.DISABLE 后 SWD 保持打开 |
  | `0x1000120C` | `0xFFFFFFFE` | NFCPINS PROTECT = 0：P0.09 / P0.10 当 GPIO 用（N5 没有 NFC 天线，这两个球悬空；当 GPIO 时自检的 gpio 能查它们） |
  | `0x10001304` | `0xFFFFFFFC` | REGOUT0 VOUT = 4：VDD 3.0 V（NAND、麦、IMU 都是 3.0 V） |

  NRFFW[0] / [1] 由 bootloader 的 HEX 自己写（`0x000F4000` / `0x000FE000`）。和 LR-N4 的文件只差 NFCPINS 这一个字；生成函数先用 N4 的两个手核文件做了逐字节回归。
- **`diag`**（CPU 停住，只写 GPIO、SAADC 寄存器和 RAM 顶上一个字。结束时 GPIO 和 SAADC 写回**复位值**，不是跑之前的值；只有 RAM 那个字写回原值。**不复位**，跑完拔 Pico 断电）：

  | 项 | 怎么读 | 判据 |
  |---|---|---|
  | RESETREAS | 寄存器，逐位翻译 | 只记录 |
  | USBREGSTATUS.VBUSDETECT | 寄存器 | 插着 USB 应为 1（U1 的 AD2 球）。用 `--usb yes/no` 说明插没插，或者 `--adc` 用探针量到的 TP1 VBUS 对照 |
  | MAINREGSTATUS、POFCON、DCDCEN / DCDCEN0 | 寄存器 | 只记录 |
  | VDD | SAADC（1/6 增益、内部 0.6 V 参考） | 2.70–3.45 V；有探针 ADC 时并列 |
  | VDDH | SAADC VDDHDIV5 × 5 | 探针量到 TP11 VSYS > 3 V 而芯片看到的差 300 mV 以上判 FAIL（U1 的 Y2 球）；高压模式却没有 VDDH 也判 FAIL |
  | 电池 | SAADC AIN0 × 2 | 只记录 |
  | 28 个空球（含 UART 两个调试脚） | 全部上拉读一次、下拉读一次 | 上拉读 0 = 对地（或和接地的网连锡），下拉读 1 = 对电源 |
  | MEM_EN（R25 1 MΩ）、MIC_PWR（R31 100 kΩ） | 上拉充电后松开再读，两个空球做对照 | 松开后变低 = 球和下拉电阻都在 |
  | 接了器件的线 | 不加上下拉读一次 | 只记录（不给没上电的器件灌电） |

  **板子的 USB 只在焊 B 面之前可以和 Pico 的 3V3 同时接**：那时 D2 没焊，USB 只把 5 V 送到 U1 的 VBUS 球，VDD 仍是 Pico 供。焊了 B 面以后插 USB，REG0 会和 Pico 抢着驱动 VDD；`diag` 看到 VDDH 有电会提示立刻拔掉。
- **lr-probe 的 ADC**（`--adc auto` 或 `--adc /dev/cu.usbmodemXXXX`）：GP26 经 10 kΩ 接 VDD（×1）、GP27 经 100k/100k 接 TP11 VSYS（×2）、GP28 经 100k/100k 接 TP1 VBUS（×2），取 1.5 s 的中位数。

### 自检固件（`--n5`）

```bash
python3 firmware/selftest/selftest.py build --n5             # build-n5/zephyr/zephyr.uf2
uv run firmware/selftest/selftest.py flash SERIAL --n5       # 拆光 Pico 的线、插 USB：板子停在 NICENANO 盘
uv run firmware/selftest/selftest.py run SERIAL --n5 [--no-dcdc] [--flip 20] [--cover 5]
```

N4 的各项照旧（芯片和 UICR 另查 NFCPINS 的 GPIO 位、晶振、射频、GPIO、VDD、VDDH、电池、CHRG、两颗麦、IMU、开关、两颗灯、DCDC），SD 卡换成：

| 项目 | 怎么测 | 通过标准 |
|---|---|---|
| NAND（`nand`） | MEM_EN 拉高；JEDEC ID（9Fh）；写 SR-1 = 0、SR-2 开 ECC 和 BUF（和录音固件一样）；从最后一块往前找第一块好块（第一页 0x800 处是 0xFF）且是空块；擦除、写第一页和最后一页、经 ECC 读回比对、再擦掉（留成原样）。最后 16 块都有数据时不写（录音的环形日志）；`nand w` 强制用最后一块好块，会丢掉上面的数据 | ID 是 EF AA 21 或 EF AA 22，擦写读全过，读回没有不可纠的 ECC 错（W25N02KV 的 ECC 状态 11 算已纠正，和录音固件 `nand_card.c` 一样）；找空块时芯片还 BUSY 就判 `busy`，不往下擦 |
| MEM_VDD | 上电前和断电后 300 ms 各读一次 /CS（R26 10 kΩ 上拉到 MEM_VDD，不加片内上下拉） | 上电时读 1；断电后读 0（读 1 报 LOOK，用表量 TP4） |
| MIC_VDD | 两颗麦都有数据 | 同两颗麦的安静项 |
| GPIO | 外设上电后应读高的线只剩 3 根：NAND /CS、IMU SDA、SCL（N5 去掉了 MOSI / MISO 的上拉）；NAND DI、DO 改成「看着的线」 | 同 N4 |

- 两颗 LDO 的输出（MEM_VDD、MIC_VDD）**SAADC 读不到**：这两个网没接到任何 AIN 脚。固件用上面的数字间接判断；要电压值，跑 `vcc 30`（两路 LDO 开 30 s），用表量 TP4（MEM_VDD）、TP5（MIC_VDD）。
- 第一块板如果没装 L1，跑 `run --no-dcdc`：REG0 开 DCDC 会把 VDD 拉塌（11-11 实验室）。
