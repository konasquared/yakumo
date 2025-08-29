#!/bin/bash

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

sudo cat <<EOF >/etc/systemd/system/yakumo.service
[Unit]
Description=Yakumo UDP Proxy Service
After=network.target

[Service]
User=root
WorkingDirectory=/opt/yakumo
ExecStart=/opt/yakumo/.venv/bin/uvicorn main:app --host 0.0.0.0 --port 3000
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable yakumo.service
sudo systemctl start yakumo.service