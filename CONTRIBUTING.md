# Contributing

How the server works inside, the code boundaries, the checks every PR runs, and how CI builds and archives the APK and
the firmware. Setting up and running a server: [README.md](README.md); the local check commands are in its
Development section.

## How the server works

The phone app uploads Opus pieces; the server keeps them on disk as a queue and feeds them in time order, detects speech locally (FireRedVAD), and transcribes in two layers. The final layer always runs: pieces of up to 5 min, cut in pauses, go to the ASR engine once each; it decides locally who is the owner and keeps conversation-local speaker ids (ReDimNet2 voiceprints), closes a conversation after a silence and summarizes it. While a page shows today, the draft layer also sends each stretch at its pause, so text shows within seconds; drafts are kept (table `drafts`) and the page shows them only where final rows do not cover the time yet.

Only speech detection and speaker identity run on this machine, both on onnxruntime (`server/infer.py`, no torch; the model files in `MODELS_DIR`, default `/opt/lr-models`, pinned by sha256: `uv run --group export python export_onnx.py` makes them); sentence splitting, diarization and transcription come from the cloud ASR engine `ENGINE` names (default `qwen`: one Alibaba Bailian / DashScope key).

Page: http://127.0.0.1:8770/ . Data, per tenant: `<data>/live.db` (SQLite: sentences, drafts, conversations, summaries, voices, the upload queue, the recorders' event logs and status reports — page `/device/<name>`; schema in `server/db.py`), `<date>.passes.jsonl` (raw engine output per pass).

Tests and tooling in `server/`: `test_live.py`, `replay_case.py` (replay a case through candidate pipelines), `score_case.py` (score against a case's `gt.jsonl`), `eval_all.sh`.

## Code boundaries and regression checks

- `server/pipeline_state.py` owns each device's typed in-memory state. Finalization and the independent draft reader keep separate cursors; persisted rows and public API payloads remain dictionaries. `server/volc_asr.py` shares the WebSocket session/packet lifecycle while keeping full-result and streaming-result accumulation separate.
- `server/db.py` applies search bounds, membership filters and result limits in SQLite (JSON1 required), with transcript fallback in the same query. Metadata matching retains Python's Unicode case handling; only returned results decode full summary bodies. Name counts read participants without loading summaries.
- `app/lib/main.dart` contains startup and the application root; `home.dart` owns navigation and the recording lifecycle. `settings.dart`, `recorder.dart`, and `recorder_status.dart` separate configuration, native-channel calls and status presentation; individual pages import their actual dependencies.
  Disposing `Home` cancels status polling and unregisters its lifecycle observer; asynchronous settings/status completions do not act on a disposed page.
- `fixtures/record-stream.json` is the common record-stream oracle for Web, Android, Swift and the Python recorder client. It covers fragmented/overlapping reads, clock anchors, gaps, truncated tails and supported boot-reuse behavior. Snapshot readers merge runs while live clients slice them; platform-specific differences are stated in the fixture rather than hidden by duplicate expectations.
- Web mutations share busy/error handling through `web/src/lib/mutation.ts`; socket retries are owned by the mounted consumer and cancelled on teardown.

PR gates run the backend suite, Web type/build checks and `*.check.ts`, Flutter analysis/tests, Android JVM regressions, Swift protocol checks, and firmware host checks (including the microphone high-pass filter). Fixture changes trigger all three protocol-consumer gates. Android JVM tests bootstrap the pinned Flutter SDK's Gradle wrapper and need no signing credentials or APK release.

The legacy `server/migrate.py` importer still refuses an existing `live.db` or pending first-layer transcripts, reads its JSON/audio inputs without changing them, and inserts audio pieces by explicit column names so added schema columns do not break the import. `test_migrate.py` exercises the real command with a temporary recording and verifies that a second invocation leaves the existing database untouched.

## GitHub Actions 构建与归档

APK 和硬件固件使用独立构建流程；构建不等于生产发布，不会上传 `/app.apk`、调用固件发布 API 或刷写板子。现有 PR 检查继续保留。

| 入口 | 构建内容 | 保存位置 |
| --- | --- | --- |
| `main` 上相关路径变更 | APK，或自上次成功构建以来有改动的固件系列（被新推送取消、失败的那次改动会补建；查不到上次成功就全部构建）的测试产物 | Actions Artifacts，保留 3 天 |
| Actions → Run workflow | 手动选择构建目标及录音器型号 | Actions Artifacts，保留 3 天 |
| `apk-v<versionName>` tag | 使用原签名的正式 APK，tag 版本须匹配 `app/pubspec.yaml` | Artifacts + Draft GitHub Release |
| `firmware-*` tag | 正式录音器型号、自检和 ESP32 中继固件 | Artifacts + Draft GitHub Release |

成功的分支构建会清理同一 workflow、同一分支的旧成功构建产物，仅保留最新一批；固件手动选型也会替换该分支之前的成功产物批次。失败或取消的构建不会触发清理。标签构建不参与清理；正式 Release 附件长期保留，上传中转 Artifacts 保留 3 天。清理任务单独授予 `actions: write`，构建任务仍为只读权限。

免费额度按账号共享，3 天保留不保证不超额。建议在账号 Billing 的 Budgets and alerts 中为 Actions 设置零超额预算并启用停止使用；此账号设置不由 workflow 管理。

正式 tag 必须指向 `main` 历史上的提交。APK 和固件分别版本化；固件内部版本继续使用 `lr.sh` 的提交时间和 hash，不改为 APK 版本号。Release 使用构建 job 上传的同一批文件，不重新编译；审阅 Draft Release 后再决定是否发布。仓库是私有的，下载需要仓库读取权限；手机和录音器继续使用现有服务端更新入口，不直接读取 GitHub。

手动入口分别为 **APK build** 和 **firmware-build**。APK 无额外参数；固件 `target` 可选 `all/devkit/selftest/blerelay`，`variant` 可选 `all/sdmod/promicro/promicro-p2/promicro-p3/expansion`（仅影响 devkit）。即使手动选择 tag，仍构建测试包，不获得正式签名或中继密钥、不创建 Release。

```bash
gh workflow run apk-build.yml --ref main
gh workflow run firmware-build.yml --ref main -f target=devkit -f variant=promicro-p3
# 正式 APK：先将 pubspec.yaml 的版本更新合入 main，再打与 versionName 匹配的 tag。
git fetch origin main
v=$(git show origin/main:app/pubspec.yaml | sed -n 's/^version: *\([0-9.]*\)+.*/\1/p')  # versionName
git tag "apk-v$v" origin/main
git push origin "apk-v$v"
# 固件归档 tag 独立命名；其设备版本仍由源码提交确定。
git tag firmware-20261014 origin/main
git push origin firmware-20261014
```

同一个正式 tag 的 Release 不覆盖重写；重跑前先检查是否已有 Draft，避免把既有版本误当作新构建。重建归档时选择新的归档 tag；正式 APK tag 仍必须匹配源码 versionName。

### Runner

CI runs on GitHub-hosted runners (`runs-on: ubuntu-24.04`). The workflows install their own toolchains: Flutter 3.47.5 (Dart 3.13.4), Android API 37.0, Temurin JDK 17.0.18+8, Nordic NCS v2.7.0 and ESP-IDF v5.5.5. Do not register self-hosted runners on this public repository: any pull request could run code on them.

### 正式构建凭据

在仓库 Settings → Secrets and variables → Actions 配置：

| Secret | 用途 |
| --- | --- |
| `ANDROID_KEYSTORE_BASE64` | 原 `live-record-debug.keystore` 的 Base64；不能新生成密钥替代 |
| `ANDROID_KEYSTORE_PASSWORD` | keystore 密码 |
| `ANDROID_KEY_ALIAS` | 原签名密钥 alias |
| `ANDROID_KEY_PASSWORD` | 原签名密钥密码 |
| `ANDROID_SIGNING_CERT_SHA256` | 原签名证书 SHA256 指纹，防止错用另一把密钥 |
| `RELAY_KEY_HEX` | 现有 ESP32 中继的 64 位十六进制认证 key |

在持有原 keystore 的机器上，可以通过 stdin 设置文件 Secret，避免把内容打印或放进命令参数：

```bash
base64 < ~/.android/live-record-debug.keystore | gh secret set ANDROID_KEYSTORE_BASE64 --repo suyiiyii/live-record
```

密码、alias 和 relay key 使用 GitHub 的 Secret 输入框或 `gh secret set` 的交互输入，不写入 shell 历史。证书指纹应从原 keystore 或已安装正式 APK 的签名证书核实，不从新生成的测试包复制。

正式 APK 缺签名凭据或证书不匹配时失败；正式 ESP32 构建缺 relay key 时失败。测试 APK 由 CI 每次随机生成的测试密钥签名，包名与正式版相同，不能覆盖原签名安装：要装测试包必须先卸载正式 App，卸载会丢掉手机上还没上传的录音分片；测试 ESP32 中继使用公开测试 key，不能当作生产认证配置。产物名称和元数据区分测试与正式用途。

ESP32 二进制包含认证 key，正式固件只能在受信任的私有仓库访问范围内分享。不要把签名文件、认证 key 或密码放入产物、缓存日志和 Git。普通构建权限为 `contents: read`，只有创建 Draft Release 的 job 使用 `contents: write`。

### 下载与安装

- 在 Actions 对应运行页面下载 Artifacts；正式归档在 Releases 的 Draft 中。过期 Artifacts 不是长期版本库。
- 录音器正式型号为 `sdmod`（LR-X1）、`promicro`（LR-P1）、`promicro-p2`（LR-P2）、`promicro-p3`（LR-P3）；`expansion` 仅用于手动测试。
- 录音器产物提供 USB 刷写用 UF2 和蓝牙升级用 DFU ZIP；自检固件单独标识，不与录音器固件混装。
- ESP32 产物保存刷写参数和原相对路径下的所有必需二进制，不只保存应用 `.bin`。
- 每组产物包含源码提交、版本、型号、工具链和 SHA256。正式 APK 更新时仍需递增 `app/pubspec.yaml` 中 `+` 后的 build number。
- 构建成功只证明编译和打包；USB/蓝牙刷写、录音、SD 卡及硬件行为仍须在实物上验收。生产发布仍按现有 APK 和固件发布流程明确执行。

## Commit messages

Keep the existing form `[type][Area] subject`, in English, imperative, subject at most 72 characters; details go in the body.

- type: `feat`, `fix`, `chore`, `ci`, `perf`, `doc`, `test`
- Area: `Server`, `App`, `Firmware`, `Web`, `CI`, … (several may stack: `[fix][Server][Web]`)
- example: `[fix][Server] Skip StepFun env checks unless the engine is stepfun`

No `Co-authored-by` trailers.
