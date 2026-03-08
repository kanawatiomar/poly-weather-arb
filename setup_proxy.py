# -*- coding: utf-8 -*-
"""
Spin up a Hetzner VPS in Germany, install SOCKS5 proxy, return connection details.
Usage: python setup_proxy.py <hetzner_api_token>
"""

import sys, time, subprocess, json
import requests
from pathlib import Path

HETZNER_API = "https://api.hetzner.cloud/v1"
SERVER_NAME = "poly-proxy"
SERVER_TYPE = "cx22"       # 2 vCPU, 4GB RAM, ~4 EUR/mo
IMAGE = "ubuntu-24.04"
LOCATION = "fsn1"          # Falkenstein, Germany

# Cloud-init script: install microsocks (tiny SOCKS5 proxy)
CLOUD_INIT = """#!/bin/bash
apt-get update -y
apt-get install -y gcc make git curl
# Install microsocks
git clone https://github.com/rofl0r/microsocks.git /opt/microsocks
cd /opt/microsocks && make
# Create systemd service
cat > /etc/systemd/system/microsocks.service << 'EOF'
[Unit]
Description=MicroSocks SOCKS5 Proxy
After=network.target

[Service]
ExecStart=/opt/microsocks/microsocks -p 1080
Restart=always

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable microsocks
systemctl start microsocks
# Allow port 1080
ufw allow 1080/tcp || true
"""


def hetzner_request(token, method, path, data=None):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    url = f"{HETZNER_API}{path}"
    r = requests.request(method, url, headers=headers, json=data, timeout=15)
    return r.json()


def main():
    if len(sys.argv) < 2:
        print("Usage: python setup_proxy.py <hetzner_api_token>")
        sys.exit(1)

    token = sys.argv[1].strip()
    print(f"Using Hetzner token: {token[:8]}...")

    # Check if server already exists
    print("Checking for existing server...")
    servers = hetzner_request(token, "GET", "/servers")
    existing = [s for s in servers.get("servers", []) if s["name"] == SERVER_NAME]
    if existing:
        server = existing[0]
        ip = server["public_net"]["ipv4"]["ip"]
        print(f"Server already exists: {ip}")
    else:
        # Create server
        print(f"Creating {SERVER_TYPE} server in {LOCATION}...")
        resp = hetzner_request(token, "POST", "/servers", {
            "name": SERVER_NAME,
            "server_type": SERVER_TYPE,
            "image": IMAGE,
            "location": LOCATION,
            "user_data": CLOUD_INIT,
        })

        if "error" in resp:
            print(f"Error creating server: {resp['error']}")
            sys.exit(1)

        server = resp["server"]
        root_password = resp.get("root_password", "")
        ip = server["public_net"]["ipv4"]["ip"]
        print(f"Server created! IP: {ip}")
        print(f"Root password: {root_password}")

        # Save credentials
        creds = {"ip": ip, "password": root_password, "token": token}
        creds_path = Path(__file__).parent / "proxy_server.json"
        with open(creds_path, "w") as f:
            json.dump(creds, f, indent=2)
        print(f"Saved to {creds_path}")

        # Wait for server to boot
        print("Waiting for server to boot (60s)...")
        time.sleep(60)

    # Update .env with proxy config
    env_path = Path(__file__).parent / ".env"
    env_content = env_path.read_text() if env_path.exists() else ""
    if "POLY_PROXY" not in env_content:
        with open(env_path, "a") as f:
            f.write(f"\nPOLY_PROXY=socks5://{ip}:1080\n")
        print(f"Added proxy to .env: socks5://{ip}:1080")
    else:
        print(f"Proxy already in .env")

    print()
    print("=" * 50)
    print(f"Proxy server ready: socks5://{ip}:1080")
    print("Now run: python place_trades.py")
    print("=" * 50)


if __name__ == "__main__":
    main()
