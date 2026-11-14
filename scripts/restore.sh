#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
    echo "Usage: $0 BACKUP_ARCHIVE.tar.gz" >&2
    exit 2
fi

CALLER_DIR=$(pwd -P)
case $1 in
    /*) ARCHIVE_PATH=$1 ;;
    *) ARCHIVE_PATH=$CALLER_DIR/$1 ;;
esac
if [ ! -f "$ARCHIVE_PATH" ]; then
    echo "Backup archive not found: $ARCHIVE_PATH" >&2
    exit 1
fi
ARCHIVE_DIR=$(CDPATH= cd -P "$(dirname "$ARCHIVE_PATH")" && pwd)
ARCHIVE_NAME=$(basename "$ARCHIVE_PATH")
ARCHIVE_PATH=$ARCHIVE_DIR/$ARCHIVE_NAME

SCRIPT_DIR=$(CDPATH= cd -P "$(dirname "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -P "$SCRIPT_DIR/.." && pwd)
cd "$PROJECT_ROOT"

DOCKER=${DOCKER:-docker}
BACKUP_IMAGE=${BACKUP_IMAGE:-alpine:3.21}

volume_for() {
    service_name=$1
    container_id=$("$DOCKER" compose ps -aq "$service_name" | head -n 1)
    if [ -z "$container_id" ]; then
        echo "No container found for service: $service_name" >&2
        exit 1
    fi
    volume_name=$("$DOCKER" inspect --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}' "$container_id")
    if [ -z "$volume_name" ]; then
        echo "No /data volume found for service: $service_name" >&2
        exit 1
    fi
    printf '%s\n' "$volume_name"
}

"$DOCKER" compose config --quiet
"$DOCKER" compose create server executor
SERVER_VOLUME=$(volume_for server)
EXECUTOR_VOLUME=$(volume_for executor)

# Reject absolute paths, parent traversal and entries outside the two data roots.
ARCHIVE_ENTRIES=$("$DOCKER" run --rm \
    -v "$ARCHIVE_DIR:/backup:ro" \
    "$BACKUP_IMAGE" tar -tzf "/backup/$ARCHIVE_NAME")
if [ -z "$ARCHIVE_ENTRIES" ]; then
    echo "Backup archive is empty: $ARCHIVE_PATH" >&2
    exit 1
fi
printf '%s\n' "$ARCHIVE_ENTRIES" | while IFS= read -r entry; do
    case $entry in
        server|server/*|executor|executor/*) ;;
        *) echo "Unexpected archive entry: $entry" >&2; exit 1 ;;
    esac
    case $entry in
        /*|..|../*|*/..|*/../*)
            echo "Unsafe archive entry: $entry" >&2
            exit 1
            ;;
    esac
done

restart_services() {
    "$DOCKER" compose up -d server executor >/dev/null 2>&1 || true
}
trap restart_services EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

"$DOCKER" compose stop server executor
"$DOCKER" run --rm \
    -v "$SERVER_VOLUME:/restore/server" \
    -v "$EXECUTOR_VOLUME:/restore/executor" \
    -v "$ARCHIVE_DIR:/backup:ro" \
    "$BACKUP_IMAGE" \
    sh -eu -c '
        find /restore/server -mindepth 1 -maxdepth 1 -exec rm -rf {} +
        find /restore/executor -mindepth 1 -maxdepth 1 -exec rm -rf {} +
        tar -xzf "$1" -C /restore
    ' sh "/backup/$ARCHIVE_NAME"

printf 'Restore completed: %s\n' "$ARCHIVE_PATH"
restart_services
trap - EXIT
cd "$CALLER_DIR"
