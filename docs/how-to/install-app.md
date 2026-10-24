---
type: how-to
---

# 如何安装并连接手机 App

App 录手机麦克风或蓝牙录音设备，把音频上传到你的服务器，并显示服务器的网页。界面跟随手机的系统语言：中文或英文（1.5.1 起）。

::: info 适用版本
Releases 里最新的安卓 APK 是 `apk-v1.5.1`（1.5.1+26），能连 `https://` 和 `http://`（Tailscale、局域网）服务器，见[如何让手机连上服务器](./remote-access.md)。
:::

## 安装

### 安卓

任选一种：

- 从 [Releases 页面](https://github.com/suyiiyii/alldayrec/releases)下载 `apk-v*` 标签下的 APK 安装。
- 自己构建：

  ```bash
  cd app
  PUB_HOSTED_URL=https://pub.flutter-io.cn flutter pub get --enforce-lockfile
  flutter build apk --release
  ```

- 服务器上放了 APK 时，用手机浏览器打开 `https://<你的服务器>/app.apk` 下载。网页的 **连接手机** 页也会给出这个下载按钮。

服务器提供的 APK 是第一个租户数据目录里的 `live-record.apk` 文件（`server/alldayrec/routes_pages.py`）。按快速开始部署的 Docker 里，就是 `data/main/live-record.apk`。App 会对比这个文件的版本号，提示更新。

### iOS

App 不在 App Store 上，需要用 Xcode 从源码构建安装：

1. 把 `app/ios/Flutter/Signing.local.xcconfig.example` 复制成 `app/ios/Flutter/Signing.local.xcconfig`。
2. 在里面填你的 Team ID（`DEVELOPMENT_TEAM`）。Bundle id 默认是作者的，用别的 Team 时取消注释 `LR_TEST_ID` 一行，填一个你自己的后缀。
3. 在 Xcode 里构建并安装到 iPhone。

用免费 Apple ID 装的 App 7 天后会打不开，需要重新安装；付费开发者账号没有这个限制。

## 连接服务器

1. 在电脑浏览器里，用手机能访问的地址打开网页（见[如何让手机连上服务器](./remote-access.md)），再打开 **连接手机**（`/connect`）。
2. 在 App 里打开 **我的 › 设置**，点 **扫码登录**，扫网页上的二维码。服务器地址和 Token 会自动填好并保存。

不方便扫码时，在 **设置** 里手动填 **地址**（比如 `https://your.server`）和 **Token**，再点 **保存**。

## 下一步

- 有蓝牙录音设备的话，[绑定录音设备](./bind-recorder.md)。没有的话，App 直接录手机麦克风。
- App 的设置项见[网页与 App](../reference/web-and-app.md#app-设置)。
