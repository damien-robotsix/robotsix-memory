FROM python:3.14-slim@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6 AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.5@sha256:db2d5999728c5837e1bf9ba278ee6b05cef1e95e82a20e27b0c915cb4478b9d7 /uv /usr/local/bin/uv
# git is required for uv to resolve the robotsix-http git dependency.
RUN apt-get update \
  && apt-get install -y --no-install-recommends git \
  && rm -rf /var/lib/apt/lists/*
# Build under the runtime's WORKDIR so the venv's absolute paths (script
# shebangs, pyvenv.cfg) stay valid once it is copied into the runtime stage.
WORKDIR /home/app
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen --no-install-project
COPY src/ ./src/
RUN uv sync --no-dev --frozen

FROM python:3.14-slim@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6 AS runtime
RUN useradd --create-home --uid 1000 app

# The base image still carries perl-base at the older trixie/main build
# (5.40.1-6), which the Trivy CRITICAL gate rejects for CVE-2026-13221,
# CVE-2026-42496 and CVE-2026-8376. trixie/main already publishes the patched
# 5.40.1-6+deb13u1, so upgrade it in the runtime stage — the builder stage's
# apt work does not reach the final image. Same remedy as robotsix-chat.
RUN apt-get update \
    && apt-get install --only-upgrade -y --no-install-recommends \
        perl-base="5.40.*" \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /home/app

COPY --from=builder /home/app/.venv /home/app/.venv
COPY pyproject.toml /home/app/
COPY src/ /home/app/src/

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health/live').read()"

USER app

CMD ["/home/app/.venv/bin/uvicorn", "robotsix_memory.main:app", "--host", "0.0.0.0", "--port", "8080"]
