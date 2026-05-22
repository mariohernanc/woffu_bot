#!/usr/bin/env bash
# Instalación del bot Woffu en un VPS Linux (Debian/Ubuntu)
# Requiere sudo
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/woffu-bot}"
SERVICE_USER="${SERVICE_USER:-woffu}"

echo "==> Instalando dependencias del sistema (Chromium headless las necesita)…"
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip \
    ca-certificates wget curl \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libxkbcommon0 libatspi2.0-0 libxcomposite1 libxdamage1 libxfixes3 \
    libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    fonts-liberation libdrm2 libxshmfence1 libgtk-3-0

if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
    echo "==> Creando usuario de servicio '${SERVICE_USER}'…"
    sudo useradd -r -m -d "${INSTALL_DIR}" -s /bin/bash "${SERVICE_USER}"
fi

echo "==> Preparando ${INSTALL_DIR}…"
sudo mkdir -p "${INSTALL_DIR}"
sudo chown -R "${SERVICE_USER}:${SERVICE_USER}" "${INSTALL_DIR}"

echo "==> Copiando ficheros del proyecto a ${INSTALL_DIR}…"
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
sudo cp -r "${SRC_DIR}/." "${INSTALL_DIR}/"
sudo chown -R "${SERVICE_USER}:${SERVICE_USER}" "${INSTALL_DIR}"

echo "==> Creando entorno virtual e instalando paquetes Python…"
sudo -u "${SERVICE_USER}" bash <<EOF
set -e
cd "${INSTALL_DIR}"
python3 -m venv venv
. venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
# Descarga Chromium (~150 MB) en el HOME del usuario woffu
python -m playwright install chromium
EOF

echo ""
echo "==> Instalación completa."
echo ""
echo "Siguientes pasos:"
echo "  1) sudo -u ${SERVICE_USER} cp ${INSTALL_DIR}/.env.example ${INSTALL_DIR}/.env"
echo "  2) sudo -u ${SERVICE_USER} nano ${INSTALL_DIR}/.env       # tus credenciales"
echo "  3) sudo -u ${SERVICE_USER} chmod 600 ${INSTALL_DIR}/.env"
echo "  4) sudo -u ${SERVICE_USER} nano ${INSTALL_DIR}/config.yaml # tu horario"
echo "  5) Probar navegador y selectores:"
echo "       sudo -u ${SERVICE_USER} bash -lc 'cd ${INSTALL_DIR} && . venv/bin/activate && python main.py --test-browser'"
echo "  6) Instalar como servicio:"
echo "       sudo cp ${INSTALL_DIR}/woffu-bot.service /etc/systemd/system/"
echo "       sudo systemctl daemon-reload"
echo "       sudo systemctl enable --now woffu-bot"
echo "       sudo journalctl -u woffu-bot -f"
