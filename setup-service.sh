#!/bin/bash
# setup-service.sh — Instala o portal como serviço permanente

PORTAL_DIR=$(dirname "$(readlink -f "$0")")
cat > /etc/systemd/system/backup-portal.service <<EOF
[Unit]
Description=Backup Portal
After=network.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=$PORTAL_DIR
ExecStart=$(which python3) $PORTAL_DIR/portal.py
Restart=always
RestartSec=5
# Credenciais gerenciadas via /portal_bkps/users.json (admin/backup padrão)
Environment=BKP_USER=xxxxxx
Environment=BKP_PASS=xxxxxxx
Environment=BKP_ENABLE=

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now backup-portal.service

# Agenda backup diario as 01:00
(crontab -l 2>/dev/null | grep -v "backup.py"; echo "0 1 * * * cd $PORTAL_DIR && python3 backup.py >> /portal_bkps/backup_cron.log 2>&1") | crontab -

echo "Serviço instalado! Portal rodando 24h em http://$(hostname -I | awk '{print $1}'):58000"
echo "Backup automatico agendado diariamente as 01:00"
echo "Login: admin / backup"
