# syntax=docker/dockerfile:1.7

# =========================================================
# Stage 1: builder —— 用 uv 解析依赖并导出到 /venv
# =========================================================
FROM python:3.11-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/venv

# 构建期系统依赖：chromadb / psycopg[binary] 需要 gcc + libpq
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

# 安装 uv（与本项目工具链一致，锁文件 uv.lock 生效）
COPY --from=ghcr.io/astral-sh/uv:0.4 /uv /usr/local/bin/uv

WORKDIR /app

# 先只拷贝依赖清单，最大化利用缓存层
COPY pyproject.toml uv.lock ./

# 依据锁文件安装到独立虚拟环境；--frozen 保证可重复构建
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# 再拷贝源码并将项目本身安装进 venv
COPY app ./app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# =========================================================
# Stage 2: runtime —— 仅带最小运行时依赖 + 非 root 用户
# =========================================================
FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/venv/bin:$PATH" \
    APP_HOME=/app \
    APP_PORT=8000

# 运行时系统依赖：libpq5（psycopg 运行需要），curl 供 healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1001 app \
    && useradd  --system --uid 1001 --gid app --home ${APP_HOME} app

WORKDIR ${APP_HOME}

# 从 builder 拷贝已装好的虚拟环境与源码
COPY --from=builder /venv /venv
COPY --from=builder /app/app ./app

# data/ 目录挂载点：ChromaDB / audit.jsonl 落盘位置，volume 挂载覆盖
RUN mkdir -p ${APP_HOME}/data && chown -R app:app ${APP_HOME} /venv

USER app
EXPOSE 8000

# 容器级健康探针：命中 /health 路由（K8s readinessProbe 之外的兜底）
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${APP_PORT}/health" || exit 1

# 生产入口：无 --reload；host 必须 0.0.0.0
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
