---
type: how-to
---

# How to install and connect the phone app

The app records the phone's mic or a BLE recorder, uploads the audio to your server and shows the server's web pages. Its UI is in Chinese only for now.

::: info Version
The newest Android APK on Releases is `apk-v1.5.1` (1.5.1+26). It connects to `https://` and to `http://` servers (Tailscale, LAN), see [Reach the server from a phone](./remote-access.md).
:::

## Install

### Android

Pick one way:

- Download the APK under an `apk-v*` tag from the [Releases page](https://github.com/suyiiyii/alldayrec/releases) and install it.
- Build it yourself:

  ```bash
  cd app
  PUB_HOSTED_URL=https://pub.flutter-io.cn flutter pub get --enforce-lockfile
  flutter build apk --release
  ```

- When the server hosts an APK, open `https://<your-server>/app.apk` in the phone's browser to download it. The **连接手机** (Connect phone) page of the web UI also shows this download button.

The APK the server offers is the file `live-record.apk` in the first tenant's data directory (`server/routes_pages.py`). In a Docker setup that follows the quickstart, that is `data/main/live-record.apk`. The app compares this file's version number with its own and offers the update.

### iOS

The app is not on the App Store. Build and install it from source with Xcode:

1. Copy `app/ios/Flutter/Signing.local.xcconfig.example` to `app/ios/Flutter/Signing.local.xcconfig`.
2. Fill in your Team ID (`DEVELOPMENT_TEAM`) there. The bundle id defaults to the author's; with another team, uncomment the `LR_TEST_ID` line and put in a suffix of your own.
3. Build the app in Xcode and install it on your iPhone.

An app you install with a free Apple ID stops opening after 7 days and has to be installed again; a paid developer account removes that limit.

## Connect to the server

1. In a browser on your computer, open the web UI at an address the phone can reach (see [Reach the server from a phone](./remote-access.md)), then open **连接手机** (`/connect`).
2. In the app, open **我的 › 设置** (Me › Settings), tap **扫码登录** (Scan to sign in) and scan the QR code on the web page. The app fills in and saves the server address and token for you.

If scanning isn't convenient, fill in **地址** (Address) and **Token** by hand in **设置**, then tap **保存** (Save).

## Next steps

- If you have a BLE recorder, [bind the recorder](./bind-recorder.md). If not, the app records the phone's mic directly.
- The app's settings are listed in [Web pages and app settings](../reference/web-and-app.md#app-settings).
