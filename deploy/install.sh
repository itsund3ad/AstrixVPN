#!/usr/bin/env bash
# Astrix — One-command VPS installer
# Usage: curl -fsSL https://raw.githubusercontent.com/itsund3ad/astrix/main/deploy/install.sh | bash
#   or:  bash deploy/install.sh [client|server] [--auto-config]

set -euo pipefail

ASTRIX_VERSION="$(cat "$(dirname "$0")/../VERSION" | tr -d ' \n')"
ASTRIX_REPO="https://github.com/itsund3ad/astrix"
ASTRIX_CONFIG_DIR="/etc/astrix"
ASTRIX_LOG_DIR="/var/log/astrix"
ASTRIX_PID_DIR="/var/run"
PYTHON_MIN="3.14"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }
log_step()  { echo -e "\n${CYAN}==>${NC} $*"; }

usage() {
    cat <<EOF
Astrix v${ASTRIX_VERSION} — VPS Installer

Usage: $0 [component] [options]

Components:
  client    Install Astrix VPN client (SOCKS5 proxy)
  server    Install Astrix VPN exit server
  all       Install both client and server (default)

Options:
  --auto-config   Generate configs with random keys automatically
  --help          Show this help

Examples:
  $0 client
  $0 server --auto-config
EOF
    exit 0
}

# --- Detect OS ---
detect_os() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        OS=$ID
        OS_VERSION=$VERSION_ID
    elif command -v lsb_release &>/dev/null; then
        OS=$(lsb_release -si | tr '[:upper:]' '[:lower:]')
        OS_VERSION=$(lsb_release -sr)
    else
        OS="unknown"
    fi
    log_info "Detected OS: ${OS} ${OS_VERSION}"
}

# --- Check Python version ---
check_python() {
    log_step "Checking Python version..."
    if command -v python3 &>/dev/null; then
        PY=$(python3 --version 2>&1 | awk '{print $2}')
        PY_MAJOR=$(echo "$PY" | cut -d. -f1)
        PY_MINOR=$(echo "$PY" | cut -d. -f2)
        log_info "Found Python ${PY}"
        if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 14 ]; }; then
            log_warn "Python >= ${PYTHON_MIN} recommended, installing..."
            install_python
        fi
    else
        log_warn "Python 3 not found, installing..."
        install_python
    fi
}

install_python() {
    case "$OS" in
        ubuntu|debian)
            apt-get update -qq
            apt-get install -y -qq python3 python3-pip python3-venv
            ;;
        centos|rhel|fedora)
            if command -v dnf &>/dev/null; then
                dnf install -y python3 python3-pip
            else
                yum install -y python3 python3-pip
            fi
            ;;
        alpine)
            apk add --no-cache python3 py3-pip
            ;;
        *)
            log_error "Unsupported OS: $OS. Install Python ${PYTHON_MIN}+ manually."
            exit 1
            ;;
    esac
}

# --- Install pip dependencies ---
install_deps() {
    local component=$1
    log_step "Installing pip dependencies for ${component}..."
    if [ "$component" = "client" ] || [ "$component" = "all" ]; then
        cd /tmp && rm -rf astrix-client
        cp -r "$(dirname "$0")/../astrix-client" /tmp/astrix-client
        pip3 install -e /tmp/astrix-client --quiet
    fi
    if [ "$component" = "server" ] || [ "$component" = "all" ]; then
        cd /tmp && rm -rf astrix-server
        cp -r "$(dirname "$0")/../astrix-server" /tmp/astrix-server
        pip3 install -e /tmp/astrix-server --quiet
    fi
    log_info "Dependencies installed."
}

# --- Create directories ---
create_dirs() {
    log_step "Creating directories..."
    mkdir -p "$ASTRIX_CONFIG_DIR" "$ASTRIX_LOG_DIR"
    chmod 755 "$ASTRIX_CONFIG_DIR"
    chmod 755 "$ASTRIX_LOG_DIR"
    log_info "Config: ${ASTRIX_CONFIG_DIR}"
    log_info "Logs:   ${ASTRIX_LOG_DIR}"
}

# --- Generate config ---
generate_config() {
    local component=$1
    local config_file="$ASTRIX_CONFIG_DIR/${component}.json"

    if [ -f "$config_file" ]; then
        log_warn "${config_file} already exists, skipping config generation."
        return
    fi

    log_step "Generating config: ${config_file}"

    if [ "$component" = "client" ]; then
        local key
        key=$(python3 -c "import secrets; print(secrets.token_hex(32))")
        cat > "$config_file" <<CONFIGEOF
{
    "socks_host": "127.0.0.1",
    "socks_port": 1080,
    "socks_user": "",
    "socks_pass": "",
    "google_host": "216.239.38.120",
    "sni": ["www.google.com"],
    "script_keys": [],
    "tunnel_key": "${key}",
    "coalesce_step_ms": 25,
    "idle_slots_per_bucket": 2,
    "debug_timing": false
}
CONFIGEOF
        log_info "Config generated with random tunnel_key."
        log_info "IMPORTANT: Edit ${config_file} and add your script_keys!"
        log_info "  nano ${config_file}"
    else
        local key
        key=$(python3 -c "import secrets; print(secrets.token_hex(32))")
        cat > "$config_file" <<CONFIGEOF
{
    "server_host": "0.0.0.0",
    "server_port": 8443,
    "tunnel_key": "${key}",
    "upstream_proxy": "",
    "debug_timing": false
}
CONFIGEOF
        log_info "Config generated with random tunnel_key."
        log_warn "Client and server MUST use the SAME tunnel_key!"
        log_info "  Server key: ${key}"
    fi

    chmod 600 "$config_file"
}

# --- Install systemd service ---
install_systemd() {
    local component=$1
    local service_file="${component}.service"
    local src="$(dirname "$0")/${service_file}"

    if [ ! -f "$src" ]; then
        # Try to find it relative to the script
        src="$(dirname "$0")/deploy/${service_file}"
        if [ ! -f "$src" ]; then
            log_error "Cannot find ${service_file}"
            return 1
        fi
    fi

    log_step "Installing systemd service: ${service_file}..."

    # Fix paths in service file
    sed "s|/usr/local/bin/|/usr/local/bin/|g" "$src" > "/etc/systemd/system/${service_file}"

    systemctl daemon-reload
    systemctl enable "${service_file}"
    log_info "Service enabled: ${service_file}"
    log_info "Start with: systemctl start ${service_file}"
    log_info "Status:     systemctl status ${service_file}"
    log_info "Logs:       journalctl -u ${service_file} -f"
}

# --- Copy example configs ---
copy_examples() {
    local component=$1
    local config_file="$ASTRIX_CONFIG_DIR/${component}.json"

    if [ -f "$config_file" ]; then
        return
    fi

    local example_path="$(dirname "$0")/../${component}_config.example.json"
    if [ -f "$example_path" ]; then
        cp "$example_path" "$config_file"
        chmod 600 "$config_file"
        log_info "Example config copied to ${config_file}"
        log_info "Edit it before starting: nano ${config_file}"
    fi
}

# --- Print summary ---
print_summary() {
    local component=$1
    echo ""
    echo -e "${CYAN}╔══════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║        Astrix v${ASTRIX_VERSION} installed!         ║${NC}"
    echo -e "${CYAN}╚══════════════════════════════════════════════╝${NC}"
    echo ""

    if [ "$component" = "client" ] || [ "$component" = "all" ]; then
        echo -e "${GREEN}Client:${NC}"
        echo "  Config:  /etc/astrix/client.json"
        echo "  Logs:    journalctl -u astrix-client -f"
        echo "  Start:   systemctl start astrix-client"
        echo "  Test:    curl --socks5 127.0.0.1:1080 https://google.com"
        echo ""
    fi
    if [ "$component" = "server" ] || [ "$component" = "all" ]; then
        echo -e "${GREEN}Server:${NC}"
        echo "  Config:  /etc/astrix/server.json"
        echo "  Logs:    journalctl -u astrix-server -f"
        echo "  Start:   systemctl start astrix-server"
        echo "  Health:  curl http://YOUR_VPS_IP:8443/healthz"
        echo ""
    fi
    echo -e "${YELLOW}IMPORTANT:${NC}"
    echo "  - Client and Server MUST use the same tunnel_key!"
    echo "  - Edit configs before starting:"
    echo "      nano /etc/astrix/client.json"
    echo "      nano /etc/astrix/server.json"
    echo "  - Or use environment variables:"
    echo "      ASTRIX_TUNNEL_KEY=... ASTRIX_SOCKS_PORT=1080 ..."
}

# ===== MAIN =====

main() {
    local component="all"
    local auto_config=false

    while [ $# -gt 0 ]; do
        case "$1" in
            client|server|all) component="$1"; shift ;;
            --auto-config) auto_config=true; shift ;;
            --help|-h) usage ;;
            *) log_error "Unknown option: $1"; usage ;;
        esac
    done

    # Require root
    if [ "$EUID" -ne 0 ]; then
        log_error "Please run as root (sudo)."
        exit 1
    fi

    echo -e "${CYAN}"
    echo "╔══════════════════════════════════════════════╗"
    echo "║         Astrix VPS Installer v${ASTRIX_VERSION}        ║"
    echo "╚══════════════════════════════════════════════╝"
    echo -e "${NC}"

    detect_os
    check_python
    create_dirs
    install_deps "$component"

    if [ "$auto_config" = true ]; then
        if [ "$component" = "client" ] || [ "$component" = "all" ]; then
            generate_config "client"
        fi
        if [ "$component" = "server" ] || [ "$component" = "all" ]; then
            generate_config "server"
        fi
    else
        if [ "$component" = "client" ] || [ "$component" = "all" ]; then
            copy_examples "client"
        fi
        if [ "$component" = "server" ] || [ "$component" = "all" ]; then
            copy_examples "server"
        fi
    fi

    install_systemd "astrix-${component}"

    print_summary "$component"
}

main "$@"
