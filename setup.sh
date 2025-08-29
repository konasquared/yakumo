#!/bin/bash

# Yakumo UDP Proxy Service Setup Script
# This script sets up the Yakumo service in /opt/yakumo with proper permissions and error handling

set -euo pipefail  # Exit on error, undefined variables, and pipe failures

# Colors for output
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly BLUE='\033[0;34m'
readonly NC='\033[0m' # No Color

# Configuration
readonly SERVICE_NAME="yakumo"
readonly INSTALL_DIR="/opt/yakumo"
readonly SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
readonly VENV_DIR="${INSTALL_DIR}/.venv"

# Logging functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
}

# Check if running as root or with sudo
check_privileges() {
    if [[ $EUID -ne 0 ]]; then
        log_error "This script must be run as root or with sudo"
        log_info "Usage: sudo $0"
        exit 1
    fi
}

# Check if we're in the correct directory
check_directory() {
    local current_dir=$(pwd)
    if [[ "$current_dir" != "$INSTALL_DIR" ]]; then
        log_error "Script must be run from $INSTALL_DIR"
        log_info "Current directory: $current_dir"
        log_info "Please run: cd $INSTALL_DIR && sudo ./setup.sh"
        exit 1
    fi
}

# Check for required files
check_required_files() {
    local required_files=("main.py" "requirements.txt")
    for file in "${required_files[@]}"; do
        if [[ ! -f "$file" ]]; then
            log_error "Required file not found: $file"
            exit 1
        fi
    done
    log_success "All required files found"
}

# Check system dependencies
check_dependencies() {
    log_info "Checking system dependencies..."
    
    # Check for Python 3
    if ! command -v python3 &> /dev/null; then
        log_error "Python 3 is not installed"
        log_info "Please install Python 3: apt update && apt install python3 python3-venv python3-pip"
        exit 1
    fi
    
    # Check for nftables
    if ! command -v nft &> /dev/null; then
        log_error "nftables is not installed"
        log_info "Please install nftables: apt update && apt install nftables"
        exit 1
    fi
    
    # Check for systemctl
    if ! command -v systemctl &> /dev/null; then
        log_error "systemd is not available"
        exit 1
    fi
    
    log_success "All dependencies are available"
}

# Setup Python virtual environment
setup_venv() {
    log_info "Setting up Python virtual environment..."
    
    # Remove existing venv if it exists
    if [[ -d "$VENV_DIR" ]]; then
        log_warning "Removing existing virtual environment"
        rm -rf "$VENV_DIR"
    fi
    
    # Create new virtual environment
    python3 -m venv "$VENV_DIR"
    
    # Activate and upgrade pip
    source "$VENV_DIR/bin/activate"
    pip install --upgrade pip
    
    # Install requirements
    log_info "Installing Python dependencies..."
    pip install -r requirements.txt
    
    # Install uvicorn if not already included
    if ! pip list | grep -q uvicorn; then
        log_info "Installing uvicorn..."
        pip install uvicorn
    fi
    
    deactivate
    log_success "Virtual environment setup complete"
}

# Set proper file permissions
set_permissions() {
    log_info "Setting file permissions..."
    
    # Set ownership to root
    chown -R root:root "$INSTALL_DIR"
    
    # Set directory permissions
    chmod 755 "$INSTALL_DIR"
    
    # Set file permissions
    find "$INSTALL_DIR" -type f -name "*.py" -exec chmod 644 {} \;
    find "$INSTALL_DIR" -type f -name "*.txt" -exec chmod 644 {} \;
    find "$INSTALL_DIR" -type f -name "*.md" -exec chmod 644 {} \;
    find "$INSTALL_DIR" -type f -name "*.sh" -exec chmod 755 {} \;
    
    # Ensure venv has proper permissions
    if [[ -d "$VENV_DIR" ]]; then
        chmod -R 755 "$VENV_DIR"
    fi
    
    log_success "File permissions set"
}

# Create systemd service
create_service() {
    log_info "Creating systemd service..."
    
    # Stop service if it's running
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        log_info "Stopping existing service..."
        systemctl stop "$SERVICE_NAME"
    fi
    
    # Create service file
    cat > "$SERVICE_FILE" << EOF
[Unit]
Description=Yakumo UDP Proxy Service
Documentation=https://github.com/konasquared/yakumo
After=network.target nftables.service
Wants=nftables.service

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=$INSTALL_DIR
Environment=PATH=$VENV_DIR/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=$VENV_DIR/bin/uvicorn main:app --host 0.0.0.0 --port 3000 --log-level info
ExecReload=/bin/kill -HUP \$MAINPID
Restart=always
RestartSec=5
StartLimitInterval=0

# Security settings
NoNewPrivileges=false
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$INSTALL_DIR
PrivateTmp=true
PrivateDevices=false
ProtectKernelTunables=false
ProtectKernelModules=false
ProtectControlGroups=false

# Capabilities needed for nftables
AmbientCapabilities=CAP_NET_ADMIN CAP_NET_RAW
CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_RAW

[Install]
WantedBy=multi-user.target
EOF

    log_success "Service file created"
}

# Configure systemd service
configure_service() {
    log_info "Configuring systemd service..."
    
    # Reload systemd daemon
    systemctl daemon-reload
    
    # Enable service
    systemctl enable "$SERVICE_NAME"
    
    log_success "Service configured and enabled"
}

# Start the service
start_service() {
    log_info "Starting $SERVICE_NAME service..."
    
    if systemctl start "$SERVICE_NAME"; then
        log_success "Service started successfully"
        
        # Wait a moment and check status
        sleep 2
        if systemctl is-active --quiet "$SERVICE_NAME"; then
            log_success "Service is running"
            systemctl status "$SERVICE_NAME" --no-pager -l
        else
            log_error "Service failed to start properly"
            log_info "Check logs with: journalctl -u $SERVICE_NAME -f"
            exit 1
        fi
    else
        log_error "Failed to start service"
        exit 1
    fi
}

# Check environment file
check_env_file() {
    if [[ ! -f ".env" ]]; then
        log_warning "No .env file found"
        log_info "Creating example .env file..."
        cat > .env << EOF
# Yakumo Configuration
# Set your access token below (no = characters allowed)
ACCESS_TOKEN=your_secret_access_token_here
EOF
        log_warning "Please edit .env file and set your ACCESS_TOKEN before starting the service"
        log_info "Example: ACCESS_TOKEN=mySecretToken123"
    else
        log_success "Environment file found"
    fi
}

# Cleanup function for error handling
cleanup() {
    local exit_code=$?
    if [[ $exit_code -ne 0 ]]; then
        log_error "Setup failed with exit code $exit_code"
        log_info "Check the logs above for details"
    fi
    exit $exit_code
}

# Main execution
main() {
    trap cleanup EXIT
    
    log_info "Starting Yakumo UDP Proxy Service setup..."
    
    check_privileges
    check_directory
    check_required_files
    check_dependencies
    check_env_file
    setup_venv
    set_permissions
    create_service
    configure_service
    start_service
    
    echo
    log_success "Yakumo setup completed successfully!"
    echo
    log_info "Service status: $(systemctl is-active $SERVICE_NAME)"
    log_info "Service logs: journalctl -u $SERVICE_NAME -f"
    log_info "API endpoint: http://localhost:3000"
    log_info "Health check: curl http://localhost:3000/health"
    echo
    
    if [[ -f ".env" ]] && grep -q "your_secret_access_token_here" .env; then
        log_warning "Don't forget to update your ACCESS_TOKEN in .env file!"
        log_info "After updating .env, restart the service: systemctl restart $SERVICE_NAME"
    fi
}

# Run main function
main "$@"