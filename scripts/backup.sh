#!/bin/sh
set -eu

CALLER_DIR=$(pwd -P)
SCRIPT_DIR=$(CDPATH= cd -P "$(dirname "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -P "$SCRIPT_DIR/.." && pwd)
cd "$PROJECT_ROOT"

DOCKER=${DOCKER:-docker}
BACKUP_IMAGE=${BACKUP_IMAGE:-alpine:3.21}
BACKUP_DIR=${BACKUP_DIR:-$PROJECT_ROOT/backups}

mkdir -p "$BACKUP_DIR"
BACKUP_DIR=$(CDPATH= cd -P "$BACKUP_DIR" && pwd)

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
TIMESTAMP=$(date -u +%Y%m%dT%H%M%SZ)
ARCHIVE_NAME="personal-agent-$TIMESTAMP.tar.gz"

restart_services() {
    "$DOCKER" compose up -d server executor >/dev/null 2>&1 || true
}
trap restart_services EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Stop writers so the SQLite database, WAL and artifacts form one consistent snapshot.
"$DOCKER" compose stop server executor
"$DOCKER" run --rm \
    -v "$SERVER_VOLUME:/source/server:ro" \
    -v "$EXECUTOR_VOLUME:/source/executor:ro" \
    -v "$BACKUP_DIR:/backup" \
    "$BACKUP_IMAGE" \
    tar -czf "/backup/$ARCHIVE_NAME" -C /source server executor

printf 'Backup created: %s\n' "$BACKUP_DIR/$ARCHIVE_NAME"
restart_services
trap - EXIT

# Return to the caller's directory when the script is sourced for diagnostics.
cd "$CALLER_DIR"
