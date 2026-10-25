---
type: reference
---

# 录音设备硬件

只用手机也可以录。全天佩戴可以用开源的蓝牙录音设备：nRF52840 板子 + PDM 麦克风 + microSD 卡 + 小电池。它一直往卡里录，连上手机就同步，手机不在身边时也不会丢。

完整的零件、引脚、焊接顺序和 3D 打印外壳在 [`hardware/lr-p3/README.md`](https://github.com/suyiiyii/alldayrec/blob/main/hardware/lr-p3/README.md)；固件、构建和空中升级在 [`firmware/devkit/README.md`](https://github.com/suyiiyii/alldayrec/blob/main/firmware/devkit/README.md)。这一页是摘要。

安卓 App 1.5.5 起也能直接连接飞书录音豆，见[如何绑定录音设备](../how-to/bind-recorder.md#飞书录音豆)。

## 型号

| 型号 | 板子 | 固件变体（`lr.sh`） |
|---|---|---|
| `LR-X1` | Seeed XIAO nRF52840 Sense（板载麦克风）+ SD 模块 + 电池 | `sdmod` |
| `LR-P3` | ProMicro nRF52840（兼容 nice!nano）+ SD 模块 + PDM 麦克风（SEL 接 3V）+ 拨动开关 + 电池，飞线焊好装进 3D 打印外壳 | `promicro-p3` |

每台设备的蓝牙名是型号加身份地址的末两个字节，形如 `LR-P3-XXXX`；地址跟着芯片走，刷固件不会变。

## LR-P3 零件

| 零件 | 型号 / 规格 |
|---|---|
| 主板 | ProMicro nRF52840（nice!nano v2 克隆） |
| SD 模块 | 3.3 V 迷你 TF 卡模块；卡用 2 GB SDSC 就够，固件只认 FAT32 或 exFAT |
| 麦克风 | PDM 麦克风小板 WMM7040DTHN0（SEL 接 3V） |
| 电池 | 602030 300 mAh 锂电池（自带保护板） |
| 开关 | 三脚拨动开关（SPDT，间距 2.54 mm），只接信号，不串在电池上 |
| 外壳 | 3D 打印卡扣外壳 v5.5L，FDM 版 36.63 × 24.7 × 18.6 mm |

## LR-P3 的灯和开关

- 开关：位置稳定 0.5 秒才算数。拨到关时，设备收尾正在写的文件、断开手机并重启；没插 USB 时随后进入关机状态（System OFF），插着 USB 时保持运行但不录音。拨到开或插 USB 唤醒。
- 红灯（P0.15）闪码：暂停或关机中不闪；没绑定手机每秒 1 下；卡写不进（没卡、卡满、写失败、借给 USB）每 3 秒 3 下；录音且手机连着每 3 秒 1 下；只录在卡上每 3 秒 2 下。开始录音时常亮 1 秒，拨到关时快闪 3 下。
- 蓝灯是充电芯片的，只在插 USB 时亮。
- 低电：用电池时连续 3 次每分钟读数低于 3.3 V，设备收尾并关机；插 USB 或拨开关唤醒。

## 固件

用 nRF Connect SDK v2.7.0 构建，比如 `./lr.sh build promicro-p3`、`./lr.sh flash promicro-p3`。新焊好的板子先刷 [`firmware/selftest`](https://github.com/suyiiyii/alldayrec/tree/main/firmware/selftest) 跑整机自检。固件最初基于 [Omi](https://github.com/BasedHardware/omi) devkit。

App 的 **录音设备 › 固件更新** 列出这个型号在服务器上发布过的固件，点了才升级。
