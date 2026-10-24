# LR-P3 录音设备硬件

> **English summary.** LR-P3 is the hand-wired BLE recorder: a ProMicro nRF52840 (nice!nano v2 clone), a 3.3 V micro-SD module, a WMM7040 PDM microphone board, an SPDT slide switch and a 602030 300 mAh LiPo, joined with flying wires and closed in a 3D-printed snap-fit case (v5.5L, 36.63 × 24.7 × 18.6 mm FDM). This page has the parts list, the pin table, the soldering order, the stack-up, the firmware build options, and the case files (STL/STEP for FDM and MJF, plus the build123d source to regenerate them). Build and flash the firmware with [`firmware/devkit/README.md`](../../firmware/devkit/README.md) (`./lr.sh build promicro-p3`).

LR-P3 是全天录的蓝牙录音设备：ProMicro nRF52840 克隆板 + 3.3 V micro-SD 模块 + WMM7040 PDM 麦克风小板 + 三脚拨动开关 + 602030 300 mAh 电池，全部用飞线手工焊在一起，装进 3D 打印的卡扣外壳。没有自己的 PCB，零件都是现成的模块。

固件的构建、刷写和空中升级见 [`firmware/devkit/README.md`](../../firmware/devkit/README.md)（变体 `promicro-p3`：`./lr.sh build promicro-p3`、`./lr.sh flash promicro-p3`），引脚定义在 [`lr_promicro.overlay`](../../firmware/devkit/overlay/lr_promicro.overlay)。焊好以后先刷 [`firmware/selftest`](../../firmware/selftest) 跑一遍整机自检（`selftest.py run SERIAL --p3`）。

## 零件

| 零件 | 型号/规格 | 备注 |
|---|---|---|
| 主板 | ProMicro nRF52840（nice!nano v2 克隆，丝印 V1840，芯片 AAD0） | Adafruit UF2 bootloader + S140 6.1.1，应用从 0x26000 开始 |
| SD 模块 | 3.3 V 迷你 TF 卡模块，排针顺序 3V3 CS MOSI CLK MISO GND，两个 M2 孔 | 卡用 2 GB SDSC 就够；固件只认 FAT32（分区类型 0x0B/0x0C）或 exFAT（0x07） |
| 麦克风 | 紫色 PDM 麦克风小板，WMM7040DTHN0（顶部进声孔），排针 3V GND SEL CLK DAT，两个 M3 孔 | 板上没有 LDO；SEL 接 3V |
| 电池 | 602030 300 mAh 锂电池（自带保护板） | 电池线最后焊 |
| 开关 | 三脚拨动开关（SPDT，间距 2.54） | 只接信号，不串在电池上 |
| 其他 | 30AWG 硅胶线（或漆包线）、kapton 胶带、0.5 mm 和 0.3 mm 泡棉、一小块耳塞泡棉（5 × 5 × 2） | |

## 接线图

照着焊的飞线图（PNG，4409 × 3149）：[wiring.png](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/hardware/lr-p3/wiring.png)。图上 ProMicro 画的是元件面（有丝印的那面），USB 朝上；麦克风模块画的是元件面，SD 模块画的是背面（丝印面）。

## 引脚表

孔号从 USB 那端往下数，B+ / B− 那一排算第 1 孔（和接线图一致）。

| 号 | 信号 | ProMicro 孔（丝印） | nRF52840 | 接到 | 线色建议 | 下料 mm |
|---|---|---|---|---|---|---|
| ① | BAT− | 右列 GND（第 3 孔） | — | 电池黑线 | 原线 | 25 |
| ② | BAT+ | 右列 B+（第 1 孔） | — | 电池红线 | 原线 | 20 |
| ③ | MIC_DAT | 左列 006 | P0.06 | 麦板 DAT | 绿 | 55 |
| ④ | MIC_CLK | 左列 008 | P0.08 | 麦板 CLK | 黄 | 55 |
| ⑤ | MIC_GND | 左列 GND（第 4 孔） | — | 麦板 GND | 黑 | 50 |
| ⑥ | MIC_3V | 左列 017 | P0.17（GPIO 给麦供电，约 1 mA） | 麦板 3V | 红 | 60 |
| ⑦ | SEL 跳线 | 麦板 SEL | — | 麦板 3V（背面） | 红 | 10 |
| ⑧ | SD_CS | 左列 020 | P0.20 | SD CS | 白 | 55 |
| ⑨ | SD_MOSI | 左列 022 | P0.22 | SD MOSI | 蓝 | 55 |
| ⑩ | SD_SCK | 左列 024 | P0.24 | SD CLK | 紫 | 55 |
| ⑪ | SD_MISO | 左列 100 | P1.00 | SD MISO | 灰 | 55 |
| ⑫ | SD_3V3 | 右列 VCC（第 5 孔） | 由 P0.13 开关（高电平通电） | SD 3V3 | 红 | 70 |
| ⑬ | SD_GND | 左列 GND（第 5 孔） | — | SD GND | 黑 | 65 |
| ⑭ | SW_SIG | 右列 115 | P1.15（内部上拉） | 开关中脚 | 橙 | 60 |
| ⑮ | SW_GND | 右列 GND（第 3 孔，和 ① 同孔） | — | 开关边脚 A | 黑 | 45 |

- 下料长度已含约 20 mm 余量；信号线用 30AWG，颜色只是建议。⑫⑬ 绞成一股走线，到 SD 排针下面再分开。
- 板上的灯：P0.15 是红灯（USB-C 朝上、芯片那面、右边那颗），由固件控制。旁边的蓝灯是充电芯片的，只在插 USB 时亮，固件管不了。
- 电池电压：芯片内部用 SAADC 量 VDDH/5，不需要分压电阻。克隆板的 P0.04 上没有分压电阻，读它没有意义。
- 开关：合上（中脚接 GND）读低电平，算「开」；断开读高电平，算「关」。P1.15 离 32 MHz 晶振远，bootloader 0.6.0 和 0.10.0 都不把它当按键用。
- PDM：1.28 MHz 时钟，单声道，下降沿采样（LEFTFALLING），比率 80，增益 0x28（0 dB）。SEL 接 3V 是规格书允许的接法；如果接 GND，下降沿采样只是靠线电容把数据位撑住，飞线一长 CLK 就会串扰到 DAT。

## 焊接

1. **方向**：线一律从 ProMicro 元件面插进孔里，翻到背面焊、剪平；翻过来以后左右两列会互换。麦克风板和 SD 模块也是线从下面插进孔、在上面焊。
2. **先量 SEL**：焊麦克风板之前，先量 SEL 和 GND 之间的电阻。约 10 kΩ 就先拆掉板上的 103 电阻再焊；开路就直接焊。⑦ 用带皮线，在 ⑥ 之后焊。
3. **找开关的「开」**：先用万用表找出拨到哪边时中脚和 A 脚导通，那边就是「开」（录音），另一边是「关」（固件进 System OFF）。B 脚剪掉；拨柄剪到离开关本体约 2.3 mm，装进外壳后刚好露出 0.4 mm。开关只接信号，电池一直接着，所以关着也能充电。
4. **顺序**：③④⑤⑥ → 自检 → ⑧–⑬ → 自检 → 麦克风板、SD 模块那头 → ⑦ → ⑭⑮ → 刷固件试录 → 最后焊 ①②（电池线先剪好、剥好再焊；焊前量一下 B− 和 GND 之间应为 0 Ω，不是就改接 B−）。
5. **漆包线**：信号线也可以全部换成漆包线，更细、更好塞进外壳。焊点接触不稳时 8 MHz 读卡会出错，自检固件的 SD 读测试能查出来。

**危险区：**

- 017 和左列第 5 孔 GND 都挨着 32.768 kHz 晶振：只在背面焊，烙铁 ≤ 340 °C，每点 ≤ 3 s。烙铁碰到晶振曾经把一块板子弄得 USB 起不来。
- B+ 挨着 USB-C 外壳（外壳是 GND），别连锡。
- 右列第 4 孔是 RST，不能接 GND，否则会一直复位。
- RAW 是 USB 的 5 V，不要接给 3.3 V 的模块。
- 天线在板子底端（丝印 104 / 010 一带），附近不要走线。
- 要升级 bootloader（比如到 0.10.0）就在焊电池之前做：电池会盖住背面的 SWD 焊盘，盖住以后出了问题就只能靠 USB 救。

## 叠层

从底壳往盖子：ProMicro（元件面朝下，USB-C 和两颗灯朝底面）→ kapton + 0.5 mm 泡棉 → 电池 → 0.3 mm 软泡棉 → SD 模块（卡座朝下）和麦克风板并排，挂在盖子的四根柱子上 → 盖子。麦克风进声孔上压一块耳塞泡棉（5 × 5 × 2，装在盖子的泡棉仓里，压到 1.0 mm），把麦孔单独密封。

飞线从元件面进孔、在背面焊好剪平，在板子下面 1.7 mm 的空隙和北侧走线槽里走，背面保持平整垫着电池。

## 固件开关

`./lr.sh build promicro-p3` 打开的 Kconfig 选项（定义在 [`firmware/devkit/Kconfig`](../../firmware/devkit/Kconfig)，变体见 [`lr.sh`](../../firmware/devkit/lr.sh)）：

| 选项 | 用在 | 作用 |
|---|---|---|
| `CONFIG_LR_SWITCH` | LR-P3 | 开关接在 P1.15：位置稳定 0.5 s 才算数。拨到关：收尾正在写的文件、断开手机、重启。开机时开关在关：没插 USB 直接进 System OFF；插着 USB 就保持运行但不录音（USB 口仍可刷固件），直到拨到开或拔掉线。拨开关或插 USB 唤醒 |
| `CONFIG_LR_POWER_TRACE` | LR-P3 | 用电池时每分钟记一条 POWER（VDDH 电压），另记掉电预警（VDDH < 3.3 V 或 VDD < 2.8 V），都立刻写进 flash |
| `CONFIG_LR_ONE_LED` | ProMicro 系列 | P0.15 单灯闪码：暂停或关机中不闪；没绑定手机每秒 1 下；卡写不进（没卡、卡满、写失败、借给 USB）每 3 s 3 下；录音且手机连着每 3 s 1 下；只录在卡上每 3 s 2 下。开始录音时常亮 1 s，拨到关时快闪 3 下 |
| `CONFIG_LR_BATTERY_VDDH` | ProMicro 系列 | 电池接 VDDH，芯片内部量 VDDH/5 |
| `CONFIG_LR_LOW_BATTERY_MV=3300` | 所有型号（默认） | 用电池时连续 3 次每分钟读数低于 3.3 V，就收尾并进 System OFF，插 USB 或拨开关唤醒；0 = 不关 |
| `CONFIG_CLOCK_CONTROL_NRF_K32SRC_100PPM` | ProMicro 系列 | 这类克隆板的 32.768 kHz 晶振偏 +47 到 +81 ppm，所以声明 100 ppm |

## 外壳 v5.5L

推荐用 v5.5L，这一版已经打印装机验证过（2026-10-24）：装配、锁扣、开关夹持和挂绳孔都没问题。

- **外形**：FDM 版 36.63 × 24.7 × 18.6 mm，MJF 版 36.63 × 25.3 × 19.1 mm。PLA 底壳约 4.2 g、盖子约 1.9 g。
- **打印参数（FDM）**：按 **0.12 mm 层高**设计，用 0.2 mm 层高打会偏紧。底壳立着打，盖子正面朝下打。
- **夹紧凸筋的过盈**（每侧）：开关 0.25 mm、SD 模块 0.20 mm、麦克风板 0.20 mm。SD 模块和麦克风板卡进盖子，开盖时不会掉；开关卡在盖子的护罩里。
- **开合**：底壳两条长边上各一个弹片锁扣，盖子卡上就锁住；天线端有指甲槽，用指甲撬开。
- **挂绳**：天线端端面上两个 Ø1.8 孔（离底面 13.05 mm，中心左右各 5 mm）。单股绳不超过 1.5 mm；每股不超过 0.8 mm 的细绳圈可以对折穿（雀头结）。要**先开盖再穿**：从一个孔进，用镊子在电池上方那条缝里沿端壁内侧拉到另一个孔穿出，拉直贴着端壁再合盖。绳子和扣子用非金属的，金属件别贴着天线端。
- **其他开口**：USB-C 和端面齐平，带插头导口；盖子上 Ø1.6 麦孔；底面两个透光点对着两颗灯（导光管里可以灌透明 UV 胶）。
- `mjf/` 是给 MJF（尼龙 PA12）打印服务的版本，外形略大。
- 换打印机、喷嘴、耗材或切片参数，凸筋的实际过盈会变。夹得太松或太紧就改源码里的 `sw_grip`、`board_crush`、`mb_crush`，只重打盖子。

**下载**（assets 分支上的原始文件）：

| | 底壳 | 盖子 | 装配体 |
|---|---|---|---|
| FDM | [base.stl](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/hardware/lr-p3/case-v5.5L/fdm/base.stl) · [base.step](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/hardware/lr-p3/case-v5.5L/fdm/base.step) | [lid.stl](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/hardware/lr-p3/case-v5.5L/fdm/lid.stl) · [lid.step](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/hardware/lr-p3/case-v5.5L/fdm/lid.step) | [assembled.step](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/hardware/lr-p3/case-v5.5L/fdm/assembled.step) |
| MJF | [base.stl](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/hardware/lr-p3/case-v5.5L/mjf/base.stl) · [base.step](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/hardware/lr-p3/case-v5.5L/mjf/base.step) | [lid.stl](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/hardware/lr-p3/case-v5.5L/mjf/lid.stl) · [lid.step](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/hardware/lr-p3/case-v5.5L/mjf/lid.step) | [assembled.step](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/hardware/lr-p3/case-v5.5L/mjf/assembled.step) |

STL 是打印摆放好的方向，STEP 可以导进 CAD 改。

**预览图**

合盖后的四个视角：

<img src="https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/hardware/lr-p3/renders/p3-assembled-4view.png" alt="LR-P3 case v5.5L, assembled" width="600">

FDM 打印摆放（盖子、底壳）：

<img src="https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/hardware/lr-p3/renders/p3-fdm-print-layout.png" alt="LR-P3 case v5.5L, FDM print layout" width="600">

盖子和底壳俯视：

<img src="https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/hardware/lr-p3/renders/p3-plans.png" alt="LR-P3 case v5.5L, lid and base plans" width="800">

剖面（叠层、导光管、开关）：

<img src="https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/hardware/lr-p3/renders/p3-sections.png" alt="LR-P3 case v5.5L, sections" width="800">

挂绳孔：

<img src="https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/hardware/lr-p3/renders/p3-v5.5L-lanyard.png" alt="LR-P3 case v5.5L, lanyard holes" width="800">

## 从源码重新生成

外壳用 [build123d](https://github.com/gumyr/build123d) 写成，源码在 [`case/`](case/)：每个尺寸都是 `enclosure_p3_v5_5L.py` 里的命名参数，标了 `MEASURE` 的是估计值，没有用卡尺量过。在 `case/` 目录下：

```bash
P3=v5_5L uv run --no-project --python 3.12 --with-requirements requirements-freeze.txt python check_p3.py fdm   # 干涉、锁扣、挂绳孔、壁厚
P3=v5_5L uv run --no-project --python 3.12 --with-requirements requirements-freeze.txt python export_p3.py      # 输出到 out/p3_v5_5L/{fdm,mjf}/
```

用这份依赖版本导出的 STL 和上面下载的文件逐字节相同。详见 [`case/README.md`](case/README.md)。
