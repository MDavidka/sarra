#!/usr/bin/env bash
# Safely migrate an AlmaLinux host from Podman compatibility packages to Docker CE.
# This script intentionally does not use --allowerasing and never deletes Docker data.
set -uo pipefail

DATA_PATHS=(/var/lib/docker /var/lib/containerd /var/lib/containers)
DOCKER_PACKAGES=(docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin)
REMOVE_PACKAGES=()
REMOVED_PACKAGES=()
LOG_FILE="${SYTE_DOCKER_RECOVERY_LOG:-/var/log/syte-docker-recovery.log}"

if [[ ${EUID} -ne 0 ]]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

mkdir -p "$(dirname "$LOG_FILE")" || exit 1
touch "$LOG_FILE" || exit 1
chmod 600 "$LOG_FILE" || exit 1
exec > >(tee -a "$LOG_FILE") 2>&1

fail() {
  echo "ERROR: $*"
  echo "No further package changes will be made. Full log: $LOG_FILE"
  exit 1
}

run() {
  echo "+ $*"
  "$@"
}

rpm_installed() {
  rpm -q "$1" >/dev/null 2>&1
}

rpm_requires() {
  rpm -q --whatrequires --qf '%{NAME}\n' "$1" 2>/dev/null | sort -u
}

unit_loaded() {
  systemctl show "$1" --property=LoadState --value 2>/dev/null | grep -qx loaded
}

show_inventory() {
  echo "===== PRE-CHANGE HOST INVENTORY ====="
  cat /etc/os-release
  echo "--- repositories ---"
  dnf repolist --all || true
  echo "--- enabled modules ---"
  dnf module list --enabled || true
  echo "--- relevant installed packages ---"
  rpm -qa | sort | grep -E '^(podman|podman-docker|docker|containerd|runc|conmon|buildah|fuse-overlayfs)' || true
  echo "--- package details ---"
  for package in podman-docker podman runc docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin; do
    rpm -qi "$package" 2>/dev/null || true
  done
  echo "--- services ---"
  for unit in podman.socket podman.service docker.socket docker.service containerd.service caddy.service firewalld.service; do
    printf '%s: ' "$unit"
    systemctl is-enabled "$unit" 2>&1 || true
    printf '  active: '
    systemctl is-active "$unit" 2>&1 || true
  done
  echo "--- container engines ---"
  docker version 2>&1 || true
  podman version 2>&1 || true
  echo "--- Docker containers ---"
  docker ps -a --no-trunc 2>&1 || true
  echo "--- Podman containers ---"
  podman ps -a --no-trunc 2>&1 || true
  echo "--- data paths (inspection only) ---"
  for path in "${DATA_PATHS[@]}"; do
    if [[ -e "$path" ]]; then
      stat -c '%A %U:%G %s %n' "$path"
    else
      echo "MISSING $path"
    fi
  done
  echo "===== END PRE-CHANGE INVENTORY ====="
}

[[ -x /usr/bin/dnf ]] || fail "dnf is required."
[[ -x /usr/bin/rpm ]] || fail "rpm is required."
[[ -r /etc/os-release ]] || fail "Cannot inspect /etc/os-release."
# shellcheck disable=SC1091
. /etc/os-release
[[ "${ID:-}" == "almalinux" ]] || fail "This script only changes AlmaLinux; detected ${PRETTY_NAME:-unknown}."

show_inventory
PREEXISTING_DOCKER_RUNNING="$(docker ps --format '{{.Names}}' 2>/dev/null || true)"
# A Podman compatibility wrapper can make `docker` appear installed while no
# Docker daemon exists. An unmanaged wrapper must not be deleted automatically.
if command -v docker >/dev/null 2>&1 && docker version 2>&1 | grep -q 'Podman Engine'; then
  if ! rpm_installed podman-docker; then
    fail "docker is an unmanaged Podman wrapper, not an RPM-owned podman-docker package; refusing to delete it automatically."
  fi
fi

# Never strand existing Podman containers by removing their engine/storage.
PODMAN_IDS="$(podman ps -aq 2>/dev/null || true)"
if [[ -n "$PODMAN_IDS" ]]; then
  fail "Podman containers exist (${PODMAN_IDS//$'\n'/ }); no packages were removed. Migrate those containers explicitly first."
fi

for package in podman-docker podman runc; do
  if rpm_installed "$package"; then
    REMOVE_PACKAGES+=("$package")
  fi
done

# Check the transaction's direct dependents before any removal. Unrelated
# packages are never accepted as collateral for this migration.
for package in "${REMOVE_PACKAGES[@]}"; do
  dependents="$(rpm_requires "$package" | grep -vE '^(podman-docker|podman|runc)$' || true)"
  [[ -z "$dependents" ]] || fail "Refusing to remove $package; required by unrelated installed package(s): $dependents"
done

# Stop only Podman socket/service units. Caddy and application services are not
# restarted or changed by this migration.
for unit in podman.socket podman.service; do
  if unit_loaded "$unit"; then
    run systemctl disable --now "$unit" || fail "Could not stop/disable $unit"
  fi
done

if ((${#REMOVE_PACKAGES[@]})); then
  echo "Removing only conflicting packages: ${REMOVE_PACKAGES[*]}"
  # Preview the transaction in the log; do not use --allowerasing.
  dnf remove --assumeno --no-autoremove "${REMOVE_PACKAGES[@]}" || fail "DNF removal transaction could not be resolved."
  run dnf -y remove --no-autoremove "${REMOVE_PACKAGES[@]}" || fail "Conflicting package removal failed."
  REMOVED_PACKAGES=("${REMOVE_PACKAGES[@]}")
else
  echo "No Podman/AppStream runc packages require removal."
fi

# AlmaLinux 8 container-tools can modularly filter the runc dependency used by
# containerd.io. Only disable it when it is actually enabled.
if [[ "${VERSION_ID:-}" == 8* ]] && dnf module list --enabled container-tools 2>/dev/null | grep -q container-tools; then
  run dnf -y module disable container-tools || fail "Could not disable AlmaLinux 8 container-tools module."
fi

if [[ ! -f /etc/yum.repos.d/docker-ce.repo ]] && ! dnf repolist --enabled 2>/dev/null | grep -qi docker-ce; then
  run dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo || fail "Could not add the official Docker CE repository."
fi

run dnf clean all || fail "Could not clean DNF metadata."
run dnf makecache --refresh || fail "Could not rebuild DNF metadata."
# Deliberate install: no --allowerasing. If this fails, the log preserves the
# exact DNF transaction error for a further targeted correction.
run dnf -y install "${DOCKER_PACKAGES[@]}" || fail "Docker CE installation failed. No Docker data directories were removed."

run systemctl enable --now docker || fail "Docker service could not be enabled and started."
docker info >/dev/null 2>&1 || fail "Docker daemon is not ready after installation."

printf '\n===== POST-CHANGE VERIFICATION =====\n'
echo "Removed packages: ${REMOVED_PACKAGES[*]:-none}"
echo "Installed Docker packages:"
rpm -q "${DOCKER_PACKAGES[@]}" || fail "One or more required Docker packages are not installed."
for command in 'docker version' 'docker info' 'docker compose version' 'containerd --version' 'runc --version'; do
  echo "--- $command ---"
  bash -c "$command" || fail "Verification failed: $command"
done

echo "--- Docker hello-world ---"
run docker run --rm hello-world || fail "docker run --rm hello-world failed."

echo "--- Docker containers ---"
docker ps -a --no-trunc

# Restore only containers that were already running before this repair. Do not
# start intentionally stopped containers.
if [[ -n "$PREEXISTING_DOCKER_RUNNING" ]]; then
  echo "--- Restoring pre-existing Docker containers ---"
  while IFS= read -r container; do
    [[ -n "$container" ]] || continue
    if [[ "$(docker inspect -f '{{.State.Running}}' "$container" 2>/dev/null || true)" != true ]]; then
      run docker start "$container" || fail "Previously running container $container could not be restarted."
    fi
    echo "Container $container running: $(docker inspect -f '{{.State.Running}}' "$container")"
  done <<< "$PREEXISTING_DOCKER_RUNNING"
fi

# Do not start intentionally stopped applications. Containers with restart
# policies should already have been restored by Docker; report their state.
APP_CONTAINER="${SYTE_APP_CONTAINER:-syte-9router}"
if docker inspect "$APP_CONTAINER" >/dev/null 2>&1; then
  APP_RUNNING="$(docker inspect -f '{{.State.Running}}' "$APP_CONTAINER")"
  echo "Application container $APP_CONTAINER running: $APP_RUNNING"
  if [[ "$APP_RUNNING" == true ]]; then
    HTTP_CODE="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:20128/ || true)"
    echo "Port 20128 HTTP response: ${HTTP_CODE:-unreachable}"
  else
    echo "Application container $APP_CONTAINER is stopped; it was not started to preserve operator intent."
  fi
else
  echo "Application container $APP_CONTAINER was not found; no application restart was attempted."
fi

if systemctl is-active --quiet caddy; then
  echo "Caddy service: active (left running; no restart performed)."
else
  echo "Caddy service: $(systemctl is-active caddy 2>&1 || true)"
fi

# Investigate the reported Next.js warning without modifying application files.
for container in $(docker ps --format '{{.Names}}' 2>/dev/null); do
  warning="$(docker logs --tail 300 "$container" 2>&1 | grep -E 'MODULE_TYPELESS_PACKAGE_JSON|backgroundTokenRefresh\.js' || true)"
  if [[ -n "$warning" ]]; then
    echo "Next.js module warning in $container:"
    echo "$warning"
  fi
done

for path in "${DATA_PATHS[@]}"; do
  if [[ -e "$path" ]]; then
    echo "Preserved data path: $path"
  fi
done

echo "===== RECOVERY COMPLETE ====="
echo "Full diagnostic log: $LOG_FILE"
