#!/bin/sh
# Build sibling sandbox images on first boot, then serve the control plane.
set -eu

if ! docker info >/dev/null 2>&1; then
  echo "Docker is not available. Mount /var/run/docker.sock into the web container." >&2
  exit 1
fi

build_if_missing() {
  name="$1"
  path="$2"
  if ! docker image inspect "$name" >/dev/null 2>&1; then
    echo "building ${name}"
    docker build -t "$name" "$path"
  fi
}

build_if_missing trustscout-code /opt/trustscout/sandbox/code
build_if_missing trustscout-egress /opt/trustscout/sandbox/egress
build_if_missing trustscout-browser /opt/trustscout/sandbox/browser

exec uvicorn app.main:app --host 0.0.0.0 --port 8080
