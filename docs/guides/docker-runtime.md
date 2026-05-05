# Docker Runtime

Use this when you want a clean, installed copy of Kollab inside a fresh Linux
container. The image builds wheels from the current checkout, then installs the
CLI with `pipx` from those wheels. That keeps it close to a real user install
while still testing the code you have locally.

The image also installs the local `kollabor-engine` and `kollabor-webui` wheels
into the same runtime environment so the HTTP runtime can be exercised from the
container.

## Build

```bash
scripts/docker-runtime.sh build
```

The resulting image is tagged `kollab:local-runtime` by default.

## Smoke Test

```bash
scripts/docker-runtime.sh smoke
```

The smoke command checks that the installed `kollab` command starts, can render
help, and that the engine module imports from inside the image.

## Run The CLI

```bash
scripts/docker-runtime.sh run
```

By default the wrapper:

- mounts your current directory at `/workspace`
- persists Kollab runtime state in the Docker volume `kollab-home`
- forwards common provider environment variables if they are set in your shell
- runs `kollab --no-daemon` so the app stays attached to the container TTY

To run a specific command:

```bash
scripts/docker-runtime.sh run kollab --help
scripts/docker-runtime.sh shell
```

## Provider Keys

Do not bake API keys into the image. Export them in your shell before running
the container:

```bash
export OPENAI_API_KEY=...
scripts/docker-runtime.sh run
```

Or point the wrapper at an env file outside the repo:

```bash
KOLLAB_DOCKER_ENV_FILE="$HOME/.config/kollab/docker.env" \
  scripts/docker-runtime.sh run
```

## Direct Docker Commands

If you prefer raw Docker commands:

```bash
docker build --target runtime -t kollab:local-runtime .

docker run --rm -it \
  -v kollab-home:/home/kollab/.kollab \
  -v "$PWD:/workspace" \
  -w /workspace \
  -e OPENAI_API_KEY \
  kollab:local-runtime \
  kollab --no-daemon
```

## Engine Runtime

To start the engine from the container:

```bash
KOLLAB_DOCKER_PORTS="7433:7433" \
  scripts/docker-runtime.sh run \
  /home/kollab/.local/pipx/venvs/kollab/bin/python \
  -m kollabor_engine serve --host 0.0.0.0 --port 7433
```

Then check it from the host:

```bash
curl http://127.0.0.1:7433/health
```

`/ready` also checks provider credentials, so it can report
`"api_credentials":"failed"` until you pass an API key into the container.
