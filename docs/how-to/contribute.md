---
type: how-to
---

# 如何参与开发

欢迎提 [issue](https://github.com/suyiiyii/alldayrec/issues) 和 PR。代码边界、CI 流程、发布构建和它们的密钥见 [CONTRIBUTING.md](https://github.com/suyiiyii/alldayrec/blob/main/CONTRIBUTING.md)（英文）。

## 本地检查

和 CI 跑的一样：

```bash
# server/
uv run --with pytest pytest -q
# web/
pnpm install --frozen-lockfile && pnpm build
for f in *.check.ts; do node --experimental-strip-types "$f" || exit 1; done
# app/（用 pubspec.lock 里记录的 pub 源）
PUB_HOSTED_URL=https://pub.flutter-io.cn flutter pub get --enforce-lockfile
flutter analyze --no-pub && flutter test --no-pub -j 4
```

安卓 JVM 和 Swift 检查见 `.github/workflows/app.yml`；固件的主机端检查见 `.github/workflows/firmware-check.yml`。

## 文档站

文档站在 `docs/` 目录，用 VitePress 构建：

```bash
cd docs
pnpm install --frozen-lockfile
pnpm docs:dev       # 本地预览
pnpm docs:build     # 构建到 docs/.vitepress/dist，同时检查死链
```

[HTTP API](../reference/api.md) 和 [MCP 工具](../reference/mcp.md)的表格由脚本生成。改了接口后，在仓库根目录重新生成并提交：

```bash
uv run --project server python docs/gen-api.py
```

## 提交说明

- 格式 `[type][Area] subject`，英文、祈使句，主题行不超过 72 个字符，细节写在正文里。
- type：`feat`、`fix`、`chore`、`ci`、`perf`、`doc`、`test`；Area：`Server`、`App`、`Firmware`、`Web`、`CI` 等，可以叠加（`[fix][Server][Web]`）。
- 不写 `Co-authored-by`。

仓库公开前改写过 git 历史，所以历史很短。提交说明里的 `lr#123` 指以前私有仓库的 issue，不是这个仓库的。

截图等二进制文件不进 `main`：先推到 `assets` 分支（只加不改，命名 `img/<名字>-<yyyymmdd>.ext`），再用 `https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/<文件>` 引用。
