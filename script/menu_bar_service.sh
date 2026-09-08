#!/usr/bin/env bash
set -euo pipefail

AEGIS_ACTION="${1:-status}"
AEGIS_APP_NAME="Jarvis"
AEGIS_LEGACY_APP_NAME="AegisMenuBar"
AEGIS_LABEL="ai.aegis.menubar.autostart"
AEGIS_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AEGIS_SOURCE_BUNDLE="/private/tmp/$AEGIS_APP_NAME.app"
AEGIS_INSTALL_DIR="$HOME/Applications"
AEGIS_INSTALLED_BUNDLE="$AEGIS_INSTALL_DIR/$AEGIS_APP_NAME.app"
AEGIS_INSTALLED_BINARY="$AEGIS_INSTALLED_BUNDLE/Contents/MacOS/$AEGIS_APP_NAME"
AEGIS_LEGACY_INSTALLED_BUNDLE="$AEGIS_INSTALL_DIR/$AEGIS_LEGACY_APP_NAME.app"
AEGIS_DOMAIN="gui/$(id -u)"
AEGIS_AGENT_DIR="$HOME/Library/LaunchAgents"
AEGIS_AGENT_PLIST="$AEGIS_AGENT_DIR/$AEGIS_LABEL.plist"
AEGIS_LOG_DIR="$HOME/Library/Logs/Aegis"
AEGIS_TEMPORARY=""
AEGIS_STAGING_ROOT=""
AEGIS_STAGING_BUNDLE=""

cleanup() {
    if [[ -n "$AEGIS_TEMPORARY" ]]; then
        /bin/rm -f "$AEGIS_TEMPORARY"
    fi
    if [[ -n "$AEGIS_STAGING_ROOT" ]]; then
        /bin/rm -rf "$AEGIS_STAGING_ROOT"
    fi
    if [[ "$AEGIS_ACTION" == "install" && "$AEGIS_SOURCE_BUNDLE" == "/private/tmp/Jarvis.app" ]]; then
        /bin/rm -rf -- "$AEGIS_SOURCE_BUNDLE"
    fi
}

trap cleanup EXIT

installed_app_is_running() {
    local process_id
    local command_line
    while IFS= read -r process_id; do
        if [[ ! "$process_id" =~ ^[1-9][0-9]*$ ]]; then
            continue
        fi
        command_line="$(/bin/ps -p "$process_id" -o command= 2>/dev/null || true)"
        command_line="${command_line#"${command_line%%[![:space:]]*}"}"
        if [[
            "$command_line" == "$AEGIS_INSTALLED_BINARY"
            || "$command_line" == "$AEGIS_INSTALLED_BINARY "*
        ]]; then
            return 0
        fi
    done < <(pgrep -x "$AEGIS_APP_NAME" 2>/dev/null || true)
    return 1
}

write_plist() {
    local target="$1"
    /usr/bin/plutil -create xml1 "$target"
    /usr/bin/plutil -insert Label -string "$AEGIS_LABEL" "$target"
    /usr/bin/plutil -insert ProgramArguments -array "$target"
    /usr/bin/plutil -insert ProgramArguments.0 -string /usr/bin/open "$target"
    /usr/bin/plutil -insert ProgramArguments.1 -string -g "$target"
    /usr/bin/plutil -insert ProgramArguments.2 -string -n "$target"
    /usr/bin/plutil -insert ProgramArguments.3 -string "$AEGIS_INSTALLED_BUNDLE" "$target"
    /usr/bin/plutil -insert RunAtLoad -bool true "$target"
    /usr/bin/plutil -insert LimitLoadToSessionType -string Aqua "$target"
    /usr/bin/plutil -insert ProcessType -string Background "$target"
    /usr/bin/plutil -insert ThrottleInterval -integer 10 "$target"
    /usr/bin/plutil -insert Umask -integer 63 "$target"
    /usr/bin/plutil -insert StandardOutPath -string "$AEGIS_LOG_DIR/menu-bar.log" "$target"
    /usr/bin/plutil -insert StandardErrorPath -string "$AEGIS_LOG_DIR/menu-bar.error.log" "$target"
    /usr/bin/plutil -lint "$target" >/dev/null
}

validate_legacy_bundle() {
    if [[ ! -e "$AEGIS_LEGACY_INSTALLED_BUNDLE" ]]; then
        return
    fi
    if [[ -L "$AEGIS_LEGACY_INSTALLED_BUNDLE" || ! -f "$AEGIS_LEGACY_INSTALLED_BUNDLE/Contents/Info.plist" ]]; then
        echo "status=error reason=unsafe_legacy_bundle" >&2
        return 1
    fi
    local identifier
    identifier="$(/usr/bin/plutil -extract CFBundleIdentifier raw \
        "$AEGIS_LEGACY_INSTALLED_BUNDLE/Contents/Info.plist" 2>/dev/null || true)"
    if [[ "$identifier" != "ai.aegis.menubar" ]]; then
        echo "status=error reason=unexpected_legacy_bundle" >&2
        return 1
    fi
}

rollback_failed_install() {
    local reason="$1"
    local previous="$2"
    /bin/launchctl bootout "$AEGIS_DOMAIN/$AEGIS_LABEL" >/dev/null 2>&1 || true
    pkill -x "$AEGIS_APP_NAME" >/dev/null 2>&1 || true
    if [[ -e "$AEGIS_INSTALLED_BUNDLE" && ! -L "$AEGIS_INSTALLED_BUNDLE" ]]; then
        /bin/rm -rf -- "$AEGIS_INSTALLED_BUNDLE"
    fi
    if [[ ! -d "$previous" || -L "$previous" ]]; then
        echo "status=error reason=$reason rollback=no_previous_bundle" >&2
        return 1
    fi
    /bin/mv "$previous" "$AEGIS_INSTALLED_BUNDLE"
    if ! /usr/bin/codesign --verify --strict "$AEGIS_INSTALLED_BUNDLE"; then
        echo "status=error reason=$reason rollback=restored_not_started" >&2
        return 1
    fi
    if ! /bin/launchctl bootstrap "$AEGIS_DOMAIN" "$AEGIS_AGENT_PLIST"; then
        echo "status=error reason=$reason rollback=restored_service_failed" >&2
        return 1
    fi
    for _ in {1..20}; do
        if installed_app_is_running; then
            echo "status=error reason=$reason rollback=restored" >&2
            return 1
        fi
        sleep 0.25
    done
    echo "status=error reason=$reason rollback=restored_not_running" >&2
    return 1
}

install_service() {
    AEGIS_INCLUDE_PERSONAL_MODELS=1 AEGIS_EMIT_ARCHIVE=0 AEGIS_EMBED_DAEMON=1 \
        "$AEGIS_PROJECT_ROOT/script/build_and_run.sh" --package
    test -d "$AEGIS_SOURCE_BUNDLE"

    /bin/mkdir -p "$AEGIS_INSTALL_DIR" "$AEGIS_AGENT_DIR" "$AEGIS_LOG_DIR"
    /bin/chmod 700 "$AEGIS_LOG_DIR"
    AEGIS_STAGING_ROOT="$(/usr/bin/mktemp -d "$AEGIS_INSTALL_DIR/.aegis-menubar.XXXXXX")"
    AEGIS_STAGING_BUNDLE="$AEGIS_STAGING_ROOT/$AEGIS_APP_NAME.app"
    /usr/bin/ditto --norsrc "$AEGIS_SOURCE_BUNDLE" "$AEGIS_STAGING_BUNDLE"
    /usr/bin/codesign --verify --strict "$AEGIS_STAGING_BUNDLE"
    validate_legacy_bundle

    /bin/launchctl bootout "$AEGIS_DOMAIN/$AEGIS_LABEL" >/dev/null 2>&1 || true
    pkill -x "$AEGIS_APP_NAME" >/dev/null 2>&1 || true
    pkill -x "$AEGIS_LEGACY_APP_NAME" >/dev/null 2>&1 || true
    if [[ -L "$AEGIS_INSTALLED_BUNDLE" ]]; then
        echo "status=error reason=unsafe_installed_bundle" >&2
        return 1
    fi
    local previous="$AEGIS_STAGING_ROOT/previous.app"
    if [[ -e "$AEGIS_INSTALLED_BUNDLE" ]]; then
        /bin/mv "$AEGIS_INSTALLED_BUNDLE" "$previous"
    fi
    if ! /bin/mv "$AEGIS_STAGING_BUNDLE" "$AEGIS_INSTALLED_BUNDLE"; then
        if [[ -e "$previous" ]]; then
            /bin/mv "$previous" "$AEGIS_INSTALLED_BUNDLE"
        fi
        return 1
    fi
    if ! /usr/bin/codesign --verify --strict "$AEGIS_INSTALLED_BUNDLE"; then
        rollback_failed_install installed_signature_invalid "$previous"
        return 1
    fi

    AEGIS_TEMPORARY="$(/usr/bin/mktemp /private/tmp/ai.aegis.menubar.XXXXXX)"
    write_plist "$AEGIS_TEMPORARY"
    if ! /usr/bin/install -m 600 "$AEGIS_TEMPORARY" "$AEGIS_AGENT_PLIST"; then
        rollback_failed_install launch_agent_install_failed "$previous"
        return 1
    fi
    if ! /bin/launchctl bootstrap "$AEGIS_DOMAIN" "$AEGIS_AGENT_PLIST"; then
        rollback_failed_install launch_agent_bootstrap_failed "$previous"
        return 1
    fi

    for _ in {1..40}; do
        if installed_app_is_running; then
            /bin/rm -rf "$AEGIS_LEGACY_INSTALLED_BUNDLE"
            echo "status=ok service=installed"
            return
        fi
        sleep 0.25
    done
    rollback_failed_install app_not_running "$previous"
    return 1
}

status_service() {
    if ! /bin/launchctl print "$AEGIS_DOMAIN/$AEGIS_LABEL" >/dev/null 2>&1; then
        echo "status=error reason=service_not_loaded"
        return 1
    fi
    if ! installed_app_is_running; then
        echo "status=error reason=app_not_running"
        return 1
    fi
    echo "status=ok service=running"
}

restart_app() {
    local argument="$1"
    local action="$2"
    if [[ ! -d "$AEGIS_INSTALLED_BUNDLE" ]]; then
        echo "status=error reason=app_not_installed" >&2
        return 1
    fi
    /usr/bin/codesign --verify --strict "$AEGIS_INSTALLED_BUNDLE"
    pkill -x "$AEGIS_APP_NAME" >/dev/null 2>&1 || true
    pkill -x "$AEGIS_LEGACY_APP_NAME" >/dev/null 2>&1 || true
    for _ in {1..20}; do
        if ! pgrep -x "$AEGIS_APP_NAME" >/dev/null 2>&1; then
            break
        fi
        sleep 0.25
    done
    if pgrep -x "$AEGIS_APP_NAME" >/dev/null 2>&1; then
        echo "status=error reason=app_did_not_stop" >&2
        return 1
    fi
    /usr/bin/open -g -n "$AEGIS_INSTALLED_BUNDLE" --args "$argument"
    for _ in {1..20}; do
        if installed_app_is_running; then
            echo "status=ok action=$action"
            return
        fi
        sleep 0.25
    done
    echo "status=error reason=app_not_running" >&2
    return 1
}

request_permissions() {
    restart_app --request-permissions request_permissions
}

request_computer_permissions() {
    restart_app --request-computer-permissions request_computer_permissions
}

start_voice_turn() {
    restart_app --voice-turn voice_turn
}

enable_wake_word() {
    restart_app --enable-wake-word enable_wake_word
}

show_hud() {
    restart_app --hud hud
}

uninstall_service() {
    /bin/launchctl bootout "$AEGIS_DOMAIN/$AEGIS_LABEL" >/dev/null 2>&1 || true
    pkill -x "$AEGIS_APP_NAME" >/dev/null 2>&1 || true
    pkill -x "$AEGIS_LEGACY_APP_NAME" >/dev/null 2>&1 || true
    /bin/rm -f "$AEGIS_AGENT_PLIST"
    echo "status=ok service=uninstalled bundle=preserved"
}

case "$AEGIS_ACTION" in
    install)
        install_service
        ;;
    status)
        status_service
        ;;
    permissions)
        request_permissions
        ;;
    computer-permissions)
        request_computer_permissions
        ;;
    voice-turn)
        start_voice_turn
        ;;
    wake-word-on)
        enable_wake_word
        ;;
    hud)
        show_hud
        ;;
    uninstall)
        uninstall_service
        ;;
    *)
        echo "usage: $0 [install|status|permissions|computer-permissions|voice-turn|wake-word-on|hud|uninstall]" >&2
        exit 2
        ;;
esac
