#!/usr/bin/env bash
set -euo pipefail

IMAGE="${KOLLAB_DOCKER_IMAGE:-kollab:local-runtime}"
CONTEXT="${KOLLAB_DOCKER_CONTEXT:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
WORKSPACE="${KOLLAB_DOCKER_WORKSPACE:-$PWD}"
HOME_VOLUME="${KOLLAB_DOCKER_HOME_VOLUME:-kollab-home}"
CONTAINER_NAME="${KOLLAB_DOCKER_CONTAINER_NAME:-kollab-runtime}"

usage() {
    cat <<EOF
Usage: scripts/docker-runtime.sh <command> [args...]

Commands:
  build          Build the clean installed runtime image
  smoke          Verify the installed kollab command in the image
  run [cmd...]   Run kollab in the container, or run the provided command
  shell          Open a shell in the container

Environment:
  KOLLAB_DOCKER_IMAGE       Image tag, default: ${IMAGE}
  KOLLAB_DOCKER_WORKSPACE   Host path mounted at /workspace, default: current directory
  KOLLAB_DOCKER_HOME_VOLUME Named volume for /home/kollab/.kollab
  KOLLAB_DOCKER_ENV_FILE    Optional env-file passed to docker run
  KOLLAB_DOCKER_PORTS       Optional space-separated port mappings, e.g. "7433:7433 8080:8080"
EOF
}

require_docker() {
    if ! command -v docker >/dev/null 2>&1; then
        echo "docker is required but was not found in PATH" >&2
        exit 1
    fi
}

provider_env_args() {
    local env_names=(
        ANTHROPIC_API_KEY
        OPENAI_API_KEY
        GEMINI_API_KEY
        OPENROUTER_API_KEY
        KOLLAB_AZURE_API_KEY
        KOLLAB_AZURE_ENDPOINT
        KOLLAB_AZURE_DEPLOYMENT
        KOLLAB_AZURE_API_VERSION
        KOLLAB_HUB_BRIDGE_TOKEN
        KOLLAB_HUB_BRIDGE_CHAT_ID
    )

    local name
    for name in "${env_names[@]}"; do
        if [[ -n "${!name:-}" ]]; then
            printf '%s\n' "--env"
            printf '%s\n' "$name"
        fi
    done

    if [[ -n "${TERM:-}" ]]; then
        printf '%s\n' "--env"
        printf '%s\n' "TERM=$TERM"
    fi

    if [[ -n "${KOLLAB_DOCKER_ENV_FILE:-}" ]]; then
        printf '%s\n' "--env-file"
        printf '%s\n' "$KOLLAB_DOCKER_ENV_FILE"
    fi
}

port_args() {
    if [[ -z "${KOLLAB_DOCKER_PORTS:-}" ]]; then
        return 0
    fi

    local mapping
    read -r -a mappings <<< "$KOLLAB_DOCKER_PORTS"
    for mapping in "${mappings[@]}"; do
        printf '%s\n' "--publish"
        printf '%s\n' "$mapping"
    done
}

docker_run() {
    local docker_args=()
    local item

    while IFS= read -r item; do
        docker_args+=("$item")
    done < <(provider_env_args)

    while IFS= read -r item; do
        docker_args+=("$item")
    done < <(port_args)

    docker run --rm -it \
        --name "$CONTAINER_NAME" \
        --mount "type=volume,src=${HOME_VOLUME},dst=/home/kollab/.kollab" \
        --mount "type=bind,src=${WORKSPACE},dst=/workspace" \
        --workdir /workspace \
        "${docker_args[@]}" \
        "$IMAGE" "$@"
}

command="${1:-run}"
shift || true

case "$command" in
    -h|--help|help)
        usage
        ;;
    build)
        require_docker
        docker build --target runtime --tag "$IMAGE" "$CONTEXT"
        ;;
    smoke)
        require_docker
        docker run --rm "$IMAGE" kollab --version
        docker run --rm "$IMAGE" kollab --help >/dev/null
        docker run --rm "$IMAGE" \
            /home/kollab/.local/pipx/venvs/kollab/bin/python \
            -m kollabor_engine --help >/dev/null
        ;;
    run)
        require_docker
        if [[ $# -eq 0 ]]; then
            docker_run kollab --no-daemon
        else
            docker_run "$@"
        fi
        ;;
    shell)
        require_docker
        docker_run bash
        ;;
    *)
        echo "unknown command: $command" >&2
        usage >&2
        exit 2
        ;;
esac
