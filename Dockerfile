# The server image: the web pages (pnpm), the server (uv, Python 3.11) and ffmpeg; linux/amd64 and linux/arm64.
#   .github/workflows/docker.yml pushes it to ghcr.io/suyiiyii/alldayrec (docker-compose.yml pulls that)
#   docker build -t alldayrec .        (or docker compose up -d --build with build: . in docker-compose.yml)

# the pages are plain JS: built once on the build machine's own platform, the same for every target
FROM --platform=$BUILDPLATFORM node:22-slim AS web
RUN npm install -g pnpm@12.8.1  # web/package.json's devEngines
WORKDIR /src/web
COPY web/package.json web/pnpm-lock.yaml web/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm build  # -> /src/server/alldayrec/static/dist

FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.12.24 /uv /bin/uv
ENV UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH=/app/server/.venv/bin:$PATH
WORKDIR /app/server
COPY server/pyproject.toml server/uv.lock ./
# the server's dependencies with the [local] extra (ENGINE=local), not the project: it runs from here (python -m alldayrec);
# export_onnx.py's torch only on demand (uv run --group export)
RUN uv sync --locked --no-cache --no-install-project --extra local
COPY server/ ./
COPY --from=web /src/server/alldayrec/static/dist alldayrec/static/dist
ARG COMMIT=docker
RUN echo "$COMMIT" > alldayrec/COMMIT  # the version the pages show (no git here)
# DATA_DIR: tenants.json (a first start makes one), the model files, the first tenant's store, all in the data volume
ENV HOST=0.0.0.0 PORT=8770 DATA_DIR=/data MODELS_DIR=/data/models
EXPOSE 8770
VOLUME /data
CMD ["python", "-m", "alldayrec"]
