# syntax=docker/dockerfile:1

# Python 3.14 is the launch runtime (operator directive)
ARG PYTHON_IMAGE=python:3.14-slim

# BuildKit can replace this stage with an asset-only named context containing
# frontend-dist/ when reusing the same selected UI as native/service artifacts.
FROM node:24-slim AS frontend

WORKDIR /src/packages/kollabor-webui/frontend
COPY packages/kollabor-webui/frontend/package.json packages/kollabor-webui/frontend/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY packages/kollabor-webui/frontend/ ./
COPY kollabor/updates/CHANGELOG.md /src/kollabor/updates/CHANGELOG.md
RUN npm run build -- --outDir /frontend-dist

FROM ${PYTHON_IMAGE} AS wheelhouse

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        git \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --no-cache-dir --upgrade pip build

WORKDIR /src
COPY . .
COPY --from=frontend /frontend-dist /opt/kollab-frontend
ENV KOLLAB_FRONTEND_DIST=/opt/kollab-frontend

RUN mkdir -p /wheelhouse \
    && for package_dir in \
        packages/kollabor-events \
        packages/kollabor-config \
        packages/kollabor-rpc \
        packages/kollabor-ai \
        packages/kollabor-tui \
        packages/kollabor-plugins \
        packages/kollabor-agent \
        packages/kollabor-engine \
        packages/kollabor-webui \
        . \
    ; do \
        python -m build --wheel --outdir /wheelhouse "$package_dir" || exit; \
    done \
    && python - <<'PY'
from pathlib import Path

print("Built local wheels:")
for wheel in sorted(Path("/wheelhouse").glob("*.whl")):
    print(f"  {wheel.name}")
PY

FROM ${PYTHON_IMAGE} AS runtime

LABEL org.opencontainers.image.title="Kollab local runtime" \
      org.opencontainers.image.description="Clean container image that installs Kollab from locally built wheels using pipx."

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TERM=xterm-256color \
    PIPX_HOME=/home/kollab/.local/pipx \
    PIPX_BIN_DIR=/home/kollab/.local/bin \
    PATH=/home/kollab/.local/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        git \
        less \
        procps \
        ripgrep \
        tini \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --no-cache-dir --upgrade pip pipx \
    && useradd --create-home --shell /bin/bash kollab \
    && mkdir -p /workspace /home/kollab/.local/bin /home/kollab/.local/pipx \
    && chown -R kollab:kollab /workspace /home/kollab

USER kollab
WORKDIR /workspace

COPY --from=wheelhouse --chown=kollab:kollab /wheelhouse /tmp/kollabor-wheelhouse

RUN python -m pipx install \
        --pip-args="--find-links=/tmp/kollabor-wheelhouse" \
        /tmp/kollabor-wheelhouse/kollab-*.whl \
    && /home/kollab/.local/pipx/venvs/kollab/bin/python -m pip install --no-cache-dir \
        --find-links=/tmp/kollabor-wheelhouse \
        /tmp/kollabor-wheelhouse/kollabor_engine-*.whl \
        /tmp/kollabor-wheelhouse/kollabor_webui-*.whl \
    && ln -sf \
        /home/kollab/.local/pipx/venvs/kollab/bin/kollabor-webui \
        /home/kollab/.local/bin/kollabor-webui \
    && kollab --version \
    && /home/kollab/.local/pipx/venvs/kollab/bin/python -m kollabor_engine --help >/tmp/kollabor-engine-help.txt \
    && kollab --help >/tmp/kollab-help.txt \
    && rm -rf \
        /tmp/kollabor-wheelhouse \
        /home/kollab/.cache \
        /home/kollab/.config \
        /home/kollab/.kollab \
    && mkdir -p /home/kollab/.kollab

ENTRYPOINT ["tini", "--"]
CMD ["kollab", "--no-daemon"]
