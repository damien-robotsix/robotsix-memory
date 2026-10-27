FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170 AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.5@sha256:db2d5999728c5837e1bf9ba278ee6b05cef1e95e82a20e27b0c915cb4478b9d7 /uv /usr/local/bin/uv
# git is required for uv to resolve the robotsix-http git dependency.
# DL3008: git is only used in the throwaway builder stage to fetch the git
# dependency; it never reaches the runtime image, and pinning a Debian
# revision would break the build whenever the archive rotates it.
# hadolint ignore=DL3008
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

FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170 AS runtime
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

# The base image's own pip (26.2.1) vendors certifi 2026.6.17, which the
# Trivy CRITICAL gate flags (GHSA-6q48-6c7w-9gw2) under the bare "Python"
# target even though the application venv already carries the fixed certifi
# (Publish Docker image run 39577829845, 2026-10-21). Nothing at runtime uses
# pip — the venv is built with uv in the builder stage — so strip it.
RUN python -m pip uninstall -y pip \
    && rm -rf /usr/local/lib/python3.14/site-packages/pip* /usr/local/bin/pip*

COPY --from=builder /home/app/.venv /home/app/.venv
COPY pyproject.toml /home/app/
COPY src/ /home/app/src/

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8080/health/live').read()"]

USER app

CMD ["/home/app/.venv/bin/uvicorn", "robotsix_memory.main:app", "--host", "0.0.0.0", "--port", "8080"]
