#!/usr/bin/env bash
# Actualiza el bot desde GitHub y reinicia el servicio
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/woffu-bot}"
REPO_URL="https://github.com/mariohernanc/woffu_bot.git"
TMP_DIR="$(mktemp -d)"

echo "==> Descargando última versión desde GitHub…"
git clone --depth=1 "${REPO_URL}" "${TMP_DIR}"

echo "==> Copiando ficheros actualizados a ${INSTALL_DIR}…"
sudo cp "${TMP_DIR}/main.py"          "${INSTALL_DIR}/"
sudo cp "${TMP_DIR}/schedule.py"      "${INSTALL_DIR}/"
sudo cp "${TMP_DIR}/woffu_browser.py" "${INSTALL_DIR}/"
sudo cp "${TMP_DIR}/requirements.txt" "${INSTALL_DIR}/"
sudo cp "${TMP_DIR}/install.sh"       "${INSTALL_DIR}/"
sudo cp "${TMP_DIR}/update.sh"        "${INSTALL_DIR}/"
sudo cp "${TMP_DIR}/woffu-bot.service" "${INSTALL_DIR}/"
sudo chown -R woffu:woffu "${INSTALL_DIR}"

echo "==> Actualizando dependencias Python…"
sudo -u woffu bash -lc "
  cd ${INSTALL_DIR}
  . venv/bin/activate
  pip install -q --upgrade -r requirements.txt
"

rm -rf "${TMP_DIR}"

echo "==> Reiniciando servicio…"
sudo systemctl restart woffu-bot
sudo systemctl status woffu-bot --no-pager

echo ""
echo "Actualización completada. Logs en tiempo real:"
echo "  sudo journalctl -u woffu-bot -f"
