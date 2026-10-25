---
type: how-to
description: "在 Linux 或 macOS 机器上用 Docker Compose 长期运行 alldayrec：填 .env、启动、固定镜像版本、设时区，以及数据存在哪。"
---

# 如何用 Docker 部署

这篇讲在一台 Linux 或 macOS 机器上用 Docker Compose 长期运行 alldayrec。第一次接触的话，先跟着[第一次跑起来](../tutorial/first-run.md)走一遍。

::: info 适用版本
`docker-compose.yml` 默认用镜像 `ghcr.io/suyiiyii/alldayrec:latest`，即最新的版本标签（写这一页时是 v0.2.0）；`main` 标签跟着 main 分支。要用哪个版本见下面的[固定镜像版本](#固定镜像版本)。
:::

## 前提

- Docker（带 Compose），amd64 或 arm64。
- 一把[阿里云百炼](https://bailian.console.aliyun.com/)北京区的 API key，或者其他语音识别引擎的 key（见[如何更换语音识别引擎](./change-asr-engine.md)）。
- 一个 OpenAI 兼容的大模型接口；用百炼时同一把 key 就行。

## 步骤

1. 取得代码并复制配置模板：

   ```bash
   git clone https://github.com/suyiiyii/alldayrec && cd alldayrec
   cp .env.example .env
   ```

2. 编辑 `.env`，至少填 `DASHSCOPE_API_KEY` 和 `LLM_API_KEY`。用 `.env.example` 里的大模型设置（百炼兼容模式、`qwen-plus`）时，两者填同一把 key。所有项见[配置项](../reference/configuration.md)。

   注释要单独占一行：Compose 会把 `VAR=   # 注释` 读成值 `# 注释`。

3. 生成 `data/tenants.json`，里面是你的 token：

   ```bash
   mkdir -p data && echo '[{"id": "main", "token": "'$(openssl rand -hex 16)'", "data": "/data/main", "recordings": "/data/main/recordings"}]' > data/tenants.json && chmod 600 data/tenants.json
   ```

4. 启动并看日志：

   ```bash
   docker compose up -d
   docker compose logs -f
   ```

   日志出现 `ready on 0.0.0.0:8770` 就可以用了。第一次启动会先下载模型文件（约 70 MB）。

5. 在浏览器里打开 `http://<服务器 IP>:8770/?token=<你的 token>`：在这台机器上用 `127.0.0.1`，在别的电脑上用这台机器的局域网或 Tailscale 地址。之后靠 cookie 保持登录。

`docker-compose.yml` 把 8770 端口发布在这台机器的所有网卡上，同一个局域网或 Tailscale 网络里的手机可以直接连，见[如何让手机连上服务器](./remote-access.md)。挡住别人的只有 `data/tenants.json` 里的 token，token 为空时服务不会启动。只想本机访问，把 `ports:` 下那一行改成 `"127.0.0.1:8770:8770"`；要从公网访问，在前面放一个带 TLS 的反向代理。

## 常见的调整

### key 调对话模型返回 403

如果这把百炼 key 在控制台里限定了可用模型，调对话模型（`qwen-plus`）会返回 403。换一把没有限定模型的 key，或者把 `LLM_*` 指向别的大模型服务。

### 时区

容器默认用 UTC。`.env.example` 里有 `TZ=Asia/Shanghai`：对话的日期和每日回顾的时间 `RECAP_AT` 都按这个时区算。删掉这一行就用 UTC。

### 固定镜像版本

把 `docker-compose.yml` 里 `image:` 一行的标签换成下面任意一种，然后 `docker compose up -d`：

| 标签 | 是什么 |
|---|---|
| `latest` | 最新的版本标签（`v*`） |
| `0.1.0`、`0.1` | 某个版本，或某个小版本系列的最新版 |
| `main` | main 分支最新一次构建 |
| `sha-<7 位提交号>` | main 上的某一次提交，比如 `sha-1971bca` |

这些标签由 `.github/workflows/docker.yml` 发布。

### 在本机构建镜像

把 `docker-compose.yml` 里 `# build: .` 那一行取消注释，然后：

```bash
docker compose up -d --build
```

## 数据在哪

所有数据都在 `./data`：`tenants.json`、模型文件（`data/models`），以及每个人的数据库和原始录音（快速开始里是 `data/main`）。完整的目录结构见[数据目录](../reference/data.md)，备份见[如何备份与恢复](./backup.md)。
