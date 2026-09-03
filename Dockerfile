FROM python:3.14-slim@sha256:cae66f2ef0ec51a9891263eeee7f987dacf0a9879e8aa9353d5606e0530619a5 AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.5@sha256:db2d5999728c5837e1bf9ba278ee6b05cef1e95e82a20e27b0c915cb4478b9d7 /uv /usr/local/bin/uv
# Build under the runtime's WORKDIR so the venv's absolute paths (script
# shebangs, pyvenv.cfg) stay valid once it is copied into the runtime stage.
WORKDIR /home/app
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen --no-install-project
COPY src/ ./src/
RUN uv sync --no-dev --frozen

FROM python:3.14-slim@sha256:cae66f2ef0ec51a9891263eeee7f987dacf0a9879e8aa9353d5606e0530619a5 AS runtime
RUN useradd --create-home --uid 1000 app
WORKDIR /home/app

COPY --from=builder /home/app/.venv /home/app/.venv
COPY pyproject.toml /home/app/
COPY src/ /home/app/src/
COPY config/ /home/app/config-defaults/

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health/live').read()"

USER app

CMD ["/home/app/.venv/bin/uvicorn", "robotsix_memory.main:app", "--host", "0.0.0.0", "--port", "8080"]
