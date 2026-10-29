# Kill switch proof image: this checkout installed into one venv (a dev
# install: console script under sys.prefix). The relay and every machine
# container run from it. Build from the repo root:
#   docker build -f tests/docker/kill_switch/Dockerfile -t kollab-killswitch:dev .
FROM python:3.14-slim
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    KOLLAB_NO_KEYRING=1 PATH=/opt/kollab/bin:$PATH
RUN apt-get update && apt-get install -y --no-install-recommends procps lsof tmux \
    && apt-get clean
COPY . /src
# No web UI needed for the proof: a stub dist satisfies the webui build hook.
RUN mkdir -p /opt/fe && echo '<!doctype html>' > /opt/fe/index.html
ENV KOLLAB_FRONTEND_DIST=/opt/fe
RUN python -m venv /opt/kollab && /opt/kollab/bin/pip install --no-cache-dir \
    /src/packages/kollabor-events /src/packages/kollabor-config /src/packages/kollabor-rpc \
    /src/packages/kollabor-ai /src/packages/kollabor-tui /src/packages/kollabor-plugins \
    /src/packages/kollabor-agent /src/packages/kollabor-engine /src/packages/kollabor-webui /src
WORKDIR /root
