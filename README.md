# vaultctl

**English** | [한국어](README.ko.md)

Simple Vault CLI for LXC environments.

A CLI tool for centrally managing secrets in Proxmox LXC containers with HashiCorp Vault.

## Table of Contents

- [Features](#features)
- [Architecture](#architecture)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Command Reference](#command-reference)
  - [User Commands](#user-commands)
  - [Admin Commands](#admin-commands)
- [Extended Commands](#extended-commands-teller-style)
- [APT Server Setup](#apt-server-setup)
- [Package Build and Deployment](#package-build-and-deployment)
- [Security Notes](#security-notes)
- [Troubleshooting](#troubleshooting)

---

## Features

- 🔐 **Simple Setup**: Single `vaultctl init` command for initial configuration
- 📦 **Secret Management**: Centralized management of environment variables per LXC
- 🐳 **Docker Support**: Automatic .env file generation
- 🔄 **Auto Token Renewal**: Automatic AppRole token reissue on expiration
- 🎯 **Single Binary**: Install without Python dependencies (deb package)
- 🚀 **Process Execution**: Run commands with injected environment variables
- 🔎 **Secret Scanning**: Search for hardcoded secrets in code (DevSecOps)
- 👁️ **Change Detection**: Auto-restart on Vault secret changes

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Admin Workstation                       │
├─────────────────────────────────────────────────────────────┤
│  vaultctl admin setup vault    # Create Policy, AppRole     │
│  vaultctl admin put lxc-161 DB_HOST=... DB_PASSWORD=...     │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    HashiCorp Vault                           │
├─────────────────────────────────────────────────────────────┤
│  proxmox/lxc/                                                │
│  ├── lxc-161  { DB_HOST, DB_PASSWORD, REDIS_URL, ... }      │
│  ├── lxc-162  { API_KEY, SECRET_KEY, ... }                  │
│  └── lxc-163  { ... }                                        │
└─────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│   LXC 161       │ │   LXC 162       │ │   LXC 163       │
│   (n8n)         │ │   (gitea)       │ │   (postgres)    │
├─────────────────┤ ├─────────────────┤ ├─────────────────┤
│ vaultctl init   │ │ vaultctl init   │ │ vaultctl init   │
│ vaultctl env    │ │ vaultctl env    │ │ vaultctl env    │
│   lxc-161       │ │   lxc-162       │ │   lxc-163       │
└─────────────────┘ └─────────────────┘ └─────────────────┘
```

---

## Installation

### Option 1: Quick Install from GitHub (Recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/meloncafe/vaultctl/main/scripts/install.sh | sudo bash
```

### Option 2: Install from Private APT Server

```bash
# Client setup (one-time)
curl -fsSL https://apt.example.com/setup-client.sh | sudo bash -s -- apt "password"

# Install
sudo apt update
sudo apt install vaultctl
```

### Option 3: Build from Source

```bash
git clone https://github.com/YOUR_USERNAME/vaultctl.git
cd vaultctl
poetry install
poetry run vaultctl --help
```

---

## Quick Start

### For Administrators

#### 1. Setup Vault (one-time)

```bash
vaultctl admin setup vault
```

This creates:
- Policy: `vaultctl` (read/write to proxmox/*)
- AppRole: `vaultctl` with Role ID and Secret ID

#### 2. Register Secrets

```bash
# Add secrets for LXC 161
vaultctl admin put lxc-161 \
  DB_HOST=postgres.internal \
  DB_PASSWORD=supersecret \
  REDIS_URL=redis://redis.internal:6379

# List all secrets
vaultctl admin list

# View specific secret
vaultctl admin get lxc-161
```

### For Users (in each LXC)

#### 1. Initial Setup (one-time)

```bash
vaultctl init
```

Interactive prompts for:
- Vault server address
- Role ID (from administrator)
- Secret ID (from administrator)

Configuration saved to `~/.config/vaultctl/config`

#### 2. Generate .env and Run

```bash
cd /opt/myapp

# Generate .env file
vaultctl env lxc-161

# Run with Docker Compose
docker compose up -d
```

Or use `vaultctl run` for direct injection:

```bash
vaultctl run lxc-161 -- docker compose up -d
```

---

## Command Reference

### User Commands

Commands for daily use in LXC containers.

| Command | Description |
|---------|-------------|
| `vaultctl init` | Initial setup (one-time) |
| `vaultctl env <name>` | Generate .env file |
| `vaultctl status` | Check connection and auth status |
| `vaultctl config` | Show current configuration |
| `vaultctl run <name> -- cmd` | Run command with injected env vars |
| `vaultctl sh <name>` | Generate shell export statements |
| `vaultctl watch <name> -- cmd` | Auto-restart on secret change |
| `vaultctl scan` | Scan code for hardcoded secrets |
| `vaultctl redact` | Mask secrets in logs |

#### vaultctl init

```bash
$ vaultctl init

🔐 Setup
╭──────────────────────────────────────╮
│ vaultctl 초기 설정                    │
│                                       │
│ Vault 연결 및 인증을 설정합니다.        │
│ 이 설정은 한 번만 하면 됩니다.          │
╰──────────────────────────────────────╯

Vault 서버 주소: https://vault.example.com
✓ 연결 성공

AppRole 인증 정보
Role ID: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
Secret ID: ********

✓ 인증 성공
  Policies: vaultctl, default
  TTL: 1시간

✓ 설정 저장: ~/.config/vaultctl/
```

#### vaultctl env

```bash
# Generate .env in current directory
vaultctl env lxc-161

# Custom output path
vaultctl env lxc-161 -o /opt/myapp/.env

# Output to stdout
vaultctl env lxc-161 --stdout
```

#### vaultctl status

```bash
$ vaultctl status

vaultctl 상태

1. 설정
   Vault: https://vault.example.com
   KV 경로: proxmox/lxc/
   설정 디렉토리: ✓ ~/.config/vaultctl

2. 연결
   ✓ Vault 서버 연결됨

3. 인증
   ✓ 인증됨
   Policies: vaultctl, default
   TTL: 58분

4. 시크릿 접근
   ✓ 접근 가능 (5개 시크릿)

✓ 모든 상태 정상
```

### Admin Commands

Commands for administrators to manage secrets and infrastructure.

| Command | Description |
|---------|-------------|
| `vaultctl admin list` | List all secrets |
| `vaultctl admin get <name>` | Get secret details |
| `vaultctl admin put <name> K=V...` | Store secrets |
| `vaultctl admin delete <name>` | Delete secret |
| `vaultctl admin import <file>` | Bulk import from JSON |
| `vaultctl admin export` | Export all to JSON |
| `vaultctl admin setup vault` | Setup Vault policy and AppRole |
| `vaultctl admin setup apt-server` | Build APT repository server |
| `vaultctl admin setup apt-client` | Configure APT client |
| `vaultctl admin repo add <pkg>` | Add package to APT repo |
| `vaultctl admin repo list` | List packages |
| `vaultctl admin repo remove <pkg>` | Remove package |
| `vaultctl admin token status` | Check token status |
| `vaultctl admin token renew` | Renew token |

#### Secret Management

```bash
# List all secrets
vaultctl admin list
vaultctl admin list -v  # verbose

# Get specific secret
vaultctl admin get lxc-161
vaultctl admin get lxc-161 -f DB_PASSWORD       # specific field
vaultctl admin get lxc-161 -f DB_PASSWORD -c    # copy to clipboard
vaultctl admin get lxc-161 --raw                # JSON output

# Store secrets
vaultctl admin put lxc-161 DB_HOST=localhost DB_PASSWORD=secret
vaultctl admin put lxc-161 NEW_KEY=value --merge    # merge with existing
vaultctl admin put lxc-161 ONLY_THIS=value --replace  # replace all

# Delete
vaultctl admin delete lxc-161
vaultctl admin delete lxc-161 --force  # no confirmation
```

#### Bulk Operations

```bash
# Export all secrets to JSON
vaultctl admin export -o secrets.json

# Import from JSON
vaultctl admin import secrets.json
vaultctl admin import secrets.json --dry-run  # validate only
```

JSON format:
```json
{
  "lxc-161": {
    "DB_HOST": "postgres.internal",
    "DB_PASSWORD": "secret123"
  },
  "lxc-162": {
    "API_KEY": "xxxx",
    "SECRET_KEY": "yyyy"
  }
}
```

#### Vault Setup

```bash
$ vaultctl admin setup vault

🔐 Vault Setup
╭──────────────────────────────────────╮
│ This will create:                    │
│ • Policy: vaultctl                   │
│ • AppRole: vaultctl-role             │
│ • KV secrets engine: proxmox/        │
╰──────────────────────────────────────╯

Vault server address [https://vault.example.com]: 
Root/Admin token: ********

1. KV Secrets Engine
   ✓ Enabled: proxmox/

2. Policy
   ✓ Created: vaultctl

3. AppRole Auth
   ✓ Enabled: approle/

4. AppRole
   ✓ Created: vaultctl

5. Credentials
────────────────────────────────────────
Save these credentials securely!
────────────────────────────────────────
  Role ID:    xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
  Secret ID:  yyyyyyyy-yyyy-yyyy-yyyy-yyyyyyyyyyyy
────────────────────────────────────────
```

---

## Extended Commands (teller-style)

Advanced features inspired by [teller](https://github.com/tellerops/teller).

### vaultctl run

Run processes with Vault environment variables injected.

```bash
# Run with injected env vars
vaultctl run lxc-161 -- node index.js
vaultctl run lxc-161 -- docker compose up -d

# Run shell command
vaultctl run lxc-161 --shell -- 'echo $DB_PASSWORD | base64'

# Reset existing env vars (isolated execution)
vaultctl run lxc-161 --reset -- python app.py
```

### vaultctl sh

Generate shell export statements for direct sourcing.

```bash
# Load env vars in current shell
eval "$(vaultctl sh lxc-161)"

# Add to .bashrc/.zshrc
echo 'eval "$(vaultctl sh lxc-161)"' >> ~/.bashrc

# Fish shell
vaultctl sh lxc-161 --format fish | source
```

### vaultctl scan

Search for hardcoded secrets in code (DevSecOps).

```bash
# Scan current directory
vaultctl scan

# Scan specific path
vaultctl scan ./src

# For CI/CD (exit 1 if secrets found)
vaultctl scan --error-if-found

# JSON output
vaultctl scan --json

# Scan specific secret only
vaultctl scan --name lxc-161
```

### vaultctl redact

Mask secrets in logs or output.

```bash
# Pipe through redact
cat app.log | vaultctl redact

# Real-time log masking
tail -f /var/log/app.log | vaultctl redact

# Process file
vaultctl redact --in dirty.log --out clean.log

# Custom mask
vaultctl redact --mask "[HIDDEN]" < input.log
```

### vaultctl watch

Auto-restart processes when Vault secrets change.

```bash
# Watch and restart on change
vaultctl watch lxc-161 -- docker compose up -d

# Custom check interval (default 60s)
vaultctl watch lxc-161 --interval 300 -- docker compose up -d

# Send SIGHUP instead of restart
vaultctl watch lxc-161 --on-change reload -- ./app
```

Register as systemd service:

```ini
# /etc/systemd/system/myapp-watcher.service
[Unit]
Description=MyApp Secret Watcher
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/vaultctl watch lxc-161 -- docker compose -f /opt/myapp/docker-compose.yml up
Restart=always
WorkingDirectory=/opt/myapp

[Install]
WantedBy=multi-user.target
```

---

## Configuration

### Configuration Files

| Path | Description |
|------|-------------|
| `~/.config/vaultctl/config` | User configuration |
| `~/.cache/vaultctl/token` | Cached token |
| `/etc/vaultctl/config` | System configuration (admin) |

### Configuration Format

```bash
# ~/.config/vaultctl/config
VAULT_ADDR=https://vault.example.com
VAULT_ROLE_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
VAULT_SECRET_ID=yyyyyyyy-yyyy-yyyy-yyyy-yyyyyyyyyyyy
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `VAULTCTL_VAULT_ADDR` | `https://vault.example.com` | Vault server address |
| `VAULTCTL_VAULT_TOKEN` | - | Vault token (optional) |
| `VAULTCTL_APPROLE_ROLE_ID` | - | AppRole Role ID |
| `VAULTCTL_APPROLE_SECRET_ID` | - | AppRole Secret ID |
| `VAULTCTL_KV_MOUNT` | `proxmox` | KV engine mount path |
| `VAULTCTL_KV_LXC_PATH` | `lxc` | Secrets path |

---

## APT Server Setup

### Build APT Server

```bash
sudo vaultctl admin setup apt-server
```

Interactive setup for:
- Web server mode (Caddy/Traefik)
- Domain configuration
- GPG signing
- Authentication

### Configure APT Client

```bash
sudo vaultctl admin setup apt-client https://apt.example.com -u apt -p "password"
```

### Manage Packages

```bash
# Add package
vaultctl admin repo add vaultctl_0.1.0_amd64.deb

# List packages
vaultctl admin repo list

# Remove package
vaultctl admin repo remove vaultctl

# Sync from GitHub releases
vaultctl admin repo sync
```

---

## Package Build and Deployment

### Build

```bash
# Clone and build
git clone https://github.com/YOUR_USERNAME/vaultctl.git
cd vaultctl
./build-deb.sh

# Result: dist/vaultctl_x.x.x_amd64.deb
```

### Deploy

```bash
# Copy to APT server
scp dist/vaultctl_*.deb root@apt-server:/tmp/

# Add to repository
ssh root@apt-server "vaultctl admin repo add /tmp/vaultctl_*.deb"

# Clients update
sudo apt update && sudo apt upgrade vaultctl
```

---

## Security Notes

### File Permissions

```bash
# User config (contains credentials)
chmod 600 ~/.config/vaultctl/config

# Token cache
chmod 600 ~/.cache/vaultctl/token
```

### Token Management

- AppRole tokens are automatically renewed on expiration
- Cached tokens are stored in `~/.cache/vaultctl/token`
- Use `vaultctl admin token status` to check token TTL

---

## Troubleshooting

### Authentication Errors

```bash
# Check status
vaultctl status

# Re-initialize
vaultctl init
```

### Connection Issues

```bash
# Test Vault connection
curl -s https://vault.example.com/v1/sys/health | jq

# Check config
vaultctl config
```

### Token Expiration

```bash
# Check token
vaultctl admin token status

# Renew token
vaultctl admin token renew

# Or re-authenticate (AppRole)
vaultctl init
```

---

## Migration from Previous Version

| Old Command | New Command |
|-------------|-------------|
| `vaultctl setup init` | `vaultctl init` |
| `vaultctl auth login` | `vaultctl init` |
| `vaultctl auth status` | `vaultctl status` |
| `vaultctl lxc list` | `vaultctl admin list` |
| `vaultctl lxc get <n>` | `vaultctl admin get <n>` |
| `vaultctl lxc put <n>` | `vaultctl admin put <n>` |
| `vaultctl docker env <n>` | `vaultctl env <n>` |
| `vaultctl token renew` | `vaultctl admin token renew` |
| `vaultctl repo add` | `vaultctl admin repo add` |

---

## License

MIT License
