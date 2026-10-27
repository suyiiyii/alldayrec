# 聆思 Arcs-Mini3 录音客户端

把聆思 Arcs-Mini3 开发板（LS26，Wi-Fi、240×240 屏、单麦、一个按键、电池）变成一台 AllDayRec 录音设备：一直录音，每 2 秒发一段 WAV 到 `POST /api/chunks`，屏幕上显示 `/ws` 推回来的实时转写和实时总结。服务端不用改，它就是又一台设备。

原厂固件的功能（唤醒词、小聆对话、拍照、配网）都还在：录音是在原厂音频回调上挂的旁路，只抄一份麦克风数据；界面是多注册的一个页面；按键只多占了原厂预留不用的「连按四下」。

## 目录

| 路径 | 内容 |
|---|---|
| `alldayrec/` | 模块本身：`adr.c`（录音旁路、上传、`/ws` 推送、数据模型、`adr` 命令）、`adr_view.c`（LVGL 页面） |
| `alldayrec/test/` | 在电脑上跑的测试，SDK 的头文件用 `test/stub/` 顶替 |
| `sdk.patch` | 对 SDK 的改动（8 个文件，见下表），基线是 SDK 的 `mini-v3.1.0` 发布（提交 `b8631d37`） |

## 构建

SDK 和工具链按聆思的文档安装（Linux；工具链约 2.8 GB，SDK 检出约 2.5 GB 加 0.8 GB 的 git 历史，每个构建目录约 400 MB），然后：

```bash
git clone https://cloud.listenai.com/CSKG836746/arcs-sdk/public/arcs_mini.git && cd arcs_mini
git checkout b8631d37                                   # release mini-v3.1.0
git apply /path/to/alldayrec/firmware/arcs-mini3/sdk.patch
ln -s /path/to/alldayrec/firmware/arcs-mini3/alldayrec apps/arcs-mini/alldayrec
bash -c "source ./arcs-sdk/env.sh && ./build.sh -S ./apps/arcs-mini -DBOARD=arcs_mini3 -B build-mini3"
```

`env.sh` 要在 bash 里 source（zsh 下它会算错 SDK 目录，报 `tools/mkhdr/mkhdr.py does not exist`）。产物是 `build-mini3/arcs-mini.bin`。

## 刷写

板子要先是 3.1.0 的基线（出厂固件更旧时，先用 SDK 的 `adb_download.sh -S res/arcs-mini3 -B build-mini3` 刷资源和程序）。之后只换程序分区（`0x600000`），提示音、唤醒词这些资源和存 Wi-Fi 的 kv 分区都不动：

```bash
adb reboot recovery                     # 会报 error: closed，板子其实已经进了 recovery，序列号变成 BOOT-*
adb -s BOOT-xxxx push build-mini3/arcs-mini.bin /RAW/NAND/600000
adb -s BOOT-xxxx shell recovery exit
adb -s BOOT-xxxx shell reboot hard
```

SDK 的 `adb_download.sh -S res/arcs-mini3 -B build-mini3 app` 做的是同样的事（要 bash 4 以上，macOS 自带的 bash 3.2 跑不了）。

## 配置

配置在 kv 里，没配或 `adr.enabled` 不是 1 时，固件和原厂完全一样。设备 shell 会回显整行命令，所以把输出丢掉，别让 token 留在终端里：

```bash
adb shell kv set string adr.url https://alldayrec.example.com > /dev/null   # 服务端地址，不带路径
adb shell kv set string adr.token "$TOKEN" > /dev/null                      # 租户的 token
adb shell kv set string adr.device arcs-mini3 > /dev/null                   # 设备名：字母、数字和 -
adb shell kv set int adr.enabled 1 > /dev/null
adb shell kv set int adr.chunk_ms 2000 > /dev/null                          # 可选：每段 1000–10000 ms，默认 2000
adb shell reboot
```

Wi-Fi 用原厂的方式连：`adb shell wifi connect <SSID> <密码>`，或连按 5–7 下进蓝牙配网。

## 使用

| 按键 | 作用 |
|---|---|
| 连按四下 | 打开 / 关上 AllDayRec 页（关上后回主页，录音照常） |
| 单击（页面打开时） | 切换「实时 / 总结 / 状态」 |
| 双击（页面打开时） | 总结、状态页往下翻一屏，到底回顶部 |
| 其它 | 原厂行为：长按关机、三击信息页、五到七击蓝牙配网、**八击以上恢复出厂（会清掉上面的 kv 配置）** |

「实时」页：白色是定稿、灰色是初稿、琥珀色是正在说的那句，新的在最下面。右上角红点表示 10 秒内有上传成功。

`adb shell adr` 看状态：上传成功 / 失败次数、最近一次的 HTTP 状态和耗时、排队和丢弃、`/ws` 是否连着，`tap:` 一行是音频回调实测的采样率（应是 16000 左右）。

## 实现要点

- 采集：原厂 `audio_stream_callback` 每次给 256 帧双声道（mic1 人声、mic2 回采），`record_samples` 数的是两个声道加起来的 512。只取 mic1，攒满一段就交给上传任务；缓冲池 12 秒，满了丢新数据并计数，绝不阻塞音频回调。每段的起始时间按样本数接续，偏离系统时钟 0.5 秒以上才重新对齐。
- 上传：SDK 的 HTTP 客户端，每段一个新连接。有分片回调时它用 `strlen()` 发第一块数据，所以第一块只放 `"RIFF"`，其余每片不超过 4096 字节（TLS 一次写得完的大小）。2xx 和 400 结束这一段，其它退避重试（1 → 8 秒）。
- 推送：nopoll WebSocket，连上发 `{"type":"watch","live":true,"wake":true}`，之后每 20 秒续一次；只处理 `device` 是自己的 `partial` / `draft` / `final` / `live`。
- 界面：LVGL 8 的 `#rrggbb 文字#` 重着色在每个折行都会重置，所以每句一个标签，用样式设颜色。
- 片上 SRAM 只剩几 KB，大的静态缓冲放在 PSRAM（`.psram.bss`）。

## 对 SDK 的改动（`sdk.patch`）

| 文件 | 改动 |
|---|---|
| `src/server/lschat_server/wakeup/app_wakeup.{c,h}` | 函数指针 `app_wakeup_record_tap`，音频回调开头调用它 |
| `apps/arcs-mini/button/app_button.c` | 原厂分发前先问 `adr_button()`，它不要的动作照原样处理 |
| `apps/arcs-mini/main.c` | 启动时调用 `adr_init()` |
| `apps/arcs-mini/services/service_power_policy.{c,h}` | `recording_active` 标志，录音时不进空闲和休眠（照小程序的 `miniapp_active`） |
| `apps/arcs-mini/CMakeLists.txt` | `add_subdirectory(alldayrec)` |
| `arcs-sdk/modules/nopoll/src/nopoll_conn.c` | 主机名不是 IP 时设置 TLS 的 SNI（SDK 原来关掉了；一台前端代理多个站点、开了 mTLS 的 Caddy 没有 SNI 会回 421） |

## 已知限制

- SDK 的 HTTP 客户端和 nopoll 默认都不校验服务端证书，token 能被中间人截走：只连自己信得过的网络和服务端。
- 在 AllDayRec 页时唤醒词是关的（原厂离开主页就关唤醒词）；在别的页面和小聆对话时，小聆的声音也会被录进去。
- 没做：停录时调 `/api/chunks/end`、状态页上的 Wi-Fi 信号和电量、设备名的 URL 编码。

测试（在 SDK 根目录，软链好模块之后）：

```bash
cc -std=gnu11 -DADR_PSRAM= -Iapps/arcs-mini/alldayrec -Iapps/arcs-mini/alldayrec/test/stub \
   -Iarcs-sdk/components/lisa_websocket -Iarcs-sdk/modules/cjson \
   apps/arcs-mini/alldayrec/test/test_adr.c arcs-sdk/modules/cjson/cJSON.c -o /tmp/test_adr && /tmp/test_adr
```
