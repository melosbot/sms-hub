# syntax=docker/dockerfile:1
#
# 多阶段构建,目标是最小运行镜像:
#   web  → 构建前端(仅 dist 进最终镜像)
#   deps → 装 Python 依赖到独立 prefix 并深度裁剪(不进最终镜像)
#   app  → python:3.12-alpine + tzdata + 裁剪后依赖 + 应用代码 + 前端产物
#
# 体积构成(预期 ~54-56MB):python:3.12-alpine 基础 ~48MB(含解释器,是天花板)
# + 裁剪后依赖 ~5MB + tzdata ~1.5MB + 应用代码与前端 ~1MB。依赖均为 musllinux
# wheel,无需 build-base 或 C 编译工具链。

# ── Stage 0:前端构建 ──────────────────────────────────────────────
FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ── Stage 1:Python 依赖(裁剪)────────────────────────────────────
FROM python:3.12-alpine AS deps
RUN apk add --no-cache binutils              # strip 调试符号需要
WORKDIR /install
COPY core/requirements.txt .
RUN pip install --no-cache-dir --no-compile --prefix=/install -r requirements.txt \
 && # 运行时用不到的全部裁掉:字节码缓存、测试目录、类型存根(.pyi)、CLI 入口脚本
    find /install -depth \( -name "__pycache__" -o -name tests -o -name test \) -exec rm -rf {} + \
 && find /install -name "*.pyc" -delete \
 && find /install -name "*.pyi" -delete \
 && rm -rf /install/bin \
 && # strip 共享库调试符号(pydantic-core 等 Rust/C 扩展的 .so)
    find /install -name "*.so" -exec strip --strip-unneeded {} + 2>/dev/null || true

# ── Stage 2:运行时(最小)─────────────────────────────────────────
FROM python:3.12-alpine
RUN apk add --no-cache tzdata                # 让 TZ / time.localtime() 生效
WORKDIR /app
COPY --from=deps /install /usr/local
COPY core/ ./core/
COPY --from=web /web/dist ./web/dist

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    WEB_DIST=/app/web/dist
EXPOSE 8025

HEALTHCHECK --interval=60s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8025/api/health',timeout=4)" || exit 1

CMD ["python", "-m", "core.main"]
