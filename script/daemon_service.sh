#!/usr/bin/env bash
set -euo pipefail

AEGIS_ACTION="${1:-status}"
AEGIS_LABEL="ai.aegis.daemon"
AEGIS_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AEGIS_PYTHON="$AEGIS_PROJECT_ROOT/.venv/bin/python"
AEGIS_DOMAIN="gui/$(id -u)"
AEGIS_AGENT_DIR="$HOME/Library/LaunchAgents"
AEGIS_AGENT_PLIST="$AEGIS_AGENT_DIR/$AEGIS_LABEL.plist"
AEGIS_LOG_DIR="$HOME/Library/Logs/Aegis"
AEGIS_TEMPORARY=""

cleanup() {
    if [[ -n "$AEGIS_TEMPORARY" ]]; then
        /bin/rm -f "$AEGIS_TEMPORARY"
    fi
}

trap cleanup EXIT

write_plist() {
    local target="$1"
    /usr/bin/plutil -create xml1 "$target"
    /usr/bin/plutil -insert Label -string "$AEGIS_LABEL" "$target"
    /usr/bin/plutil -insert ProgramArguments -array "$target"
    /usr/bin/plutil -insert ProgramArguments.0 -string "$AEGIS_PYTHON" "$target"
    /usr/bin/plutil -insert ProgramArguments.1 -string -m "$target"
    /usr/bin/plutil -insert ProgramArguments.2 -string aegis_core.cli "$target"
    /usr/bin/plutil -insert ProgramArguments.3 -string daemon "$target"
    /usr/bin/plutil -insert WorkingDirectory -string "$AEGIS_PROJECT_ROOT" "$target"
    /usr/bin/plutil -insert EnvironmentVariables -dictionary "$target"
    /usr/bin/plutil -insert EnvironmentVariables.AEGIS_WORKSPACE_ROOT \
        -string "$AEGIS_PROJECT_ROOT" "$target"
    /usr/bin/plutil -insert EnvironmentVariables.PYTHONUNBUFFERED -string 1 "$target"
    /usr/bin/plutil -insert StandardOutPath -string "$AEGIS_LOG_DIR/daemon.log" "$target"
    /usr/bin/plutil -insert StandardErrorPath -string "$AEGIS_LOG_DIR/daemon.error.log" "$target"
    /usr/bin/plutil -insert RunAtLoad -bool true "$target"
    /usr/bin/plutil -insert KeepAlive -bool true "$target"
    /usr/bin/plutil -insert ProcessType -string Background "$target"
    /usr/bin/plutil -insert ThrottleInterval -integer 5 "$target"
    /usr/bin/plutil -insert Umask -integer 63 "$target"
    /usr/bin/plutil -lint "$target" >/dev/null
}

install_service() {
    test -x "$AEGIS_PYTHON"
    /bin/mkdir -p "$AEGIS_AGENT_DIR" "$AEGIS_LOG_DIR"
    /bin/chmod 700 "$AEGIS_LOG_DIR"

    AEGIS_TEMPORARY="$(/usr/bin/mktemp /private/tmp/ai.aegis.daemon.XXXXXX)"
    write_plist "$AEGIS_TEMPORARY"

    /bin/launchctl bootout "$AEGIS_DOMAIN/$AEGIS_LABEL" >/dev/null 2>&1 || true
    /usr/bin/install -m 600 "$AEGIS_TEMPORARY" "$AEGIS_AGENT_PLIST"
    /bin/launchctl bootstrap "$AEGIS_DOMAIN" "$AEGIS_AGENT_PLIST"
    /bin/launchctl kickstart -k "$AEGIS_DOMAIN/$AEGIS_LABEL"

    for _ in {1..20}; do
        if "$AEGIS_PYTHON" -m aegis_core.cli daemon-status >/dev/null 2>&1; then
            echo "status=ok service=installed"
            return
        fi
        sleep 0.25
    done
    echo "status=error reason=daemon_not_ready" >&2
    return 1
}

status_service() {
    if ! /bin/launchctl print "$AEGIS_DOMAIN/$AEGIS_LABEL" >/dev/null 2>&1; then
        echo "status=error reason=service_not_loaded"
        return 1
    fi
    "$AEGIS_PYTHON" -m aegis_core.cli daemon-status
}

uninstall_service() {
    /bin/launchctl bootout "$AEGIS_DOMAIN/$AEGIS_LABEL" >/dev/null 2>&1 || true
    /bin/rm -f "$AEGIS_AGENT_PLIST"
    echo "status=ok service=uninstalled"
}

case "$AEGIS_ACTION" in
    install)
        install_service
        ;;
    status)
        status_service
        ;;
    uninstall)
        uninstall_service
        ;;
    *)
        echo "usage: $0 [install|status|uninstall]" >&2
        exit 2
        ;;
esac
