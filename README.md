# Woffu Auto-Sign Bot — modo navegador

Bot que automatiza el fichaje en Woffu **abriendo un navegador real**
(Chromium headless con Playwright) en lugar de llamar a la API. Simula
comportamiento humano: movimientos de ratón con curvas, tipeo carácter a
carácter con micro-pausas, scrolls suaves, esperas variables, y aplica
parches anti-detección (`navigator.webdriver` oculto, plugins simulados, etc.).

> ⚠️ **Aviso legal**: Verifica que el uso de este bot cumple la política
> interna de tu empresa y la normativa laboral aplicable. Falsear el
> registro horario puede tener consecuencias laborales y legales serias.

## Cómo funciona

1. **Programador** (`schedule.py`): lee `config.yaml`, calcula los fichajes
   de cada día aplicando jitter (variabilidad aleatoria de minutos) y
   respetando exclusiones.
2. **Cliente de navegador** (`woffu_browser.py`): cuando llega la hora,
   abre Chromium con un **perfil persistente** (cookies guardadas), va al
   dashboard, hace login si la sesión expiró y pulsa el botón de fichaje
   con movimientos y delays humanos.
3. **Bucle principal** (`main.py`): duerme entre fichajes; al despertar
   lanza el navegador, hace su faena y lo cierra. Reintentos con backoff.

### Anti-detección incluida

- Perfil persistente → cookies guardadas como un humano normal
- `navigator.webdriver = undefined`
- Plugins, languages, WebGL vendor simulados
- Argumento `--disable-blink-features=AutomationControlled`
- User-Agent realista (Chrome estable)
- Tipeo carácter a carácter con delays aleatorios (40–140 ms + micro-pausas)
- Ratón con trayectoria curvada (punto intermedio + 2 movimientos)
- Esperas humanas entre acciones (`asyncio.sleep` con valores aleatorios)
- Scrolls suaves y movimientos parásitos antes de hacer clic

## Estructura

```
woffu-bot/
├── config.yaml           # horarios, exclusiones, jitter, selectores, …
├── .env.example          # credenciales por entorno
├── main.py               # bucle, CLI
├── schedule.py           # cálculo de horario + jitter + exclusiones
├── woffu_browser.py      # cliente Playwright con comportamiento humano
├── requirements.txt
├── install.sh            # instalación automática
├── woffu-bot.service     # unidad systemd
├── browser_profile/      # se crea solo (perfil persistente del navegador)
├── logs/                 # se crea solo
└── screenshots/          # se crea solo (capturas en errores y tras fichar)
```

## Instalación rápida en VPS (Ubuntu/Debian)

```bash
# 1. Subir el proyecto al VPS
scp -r woffu-bot/ usuario@tuvps:/tmp/

# 2. Ejecutar el instalador
ssh usuario@tuvps
cd /tmp/woffu-bot
chmod +x install.sh
./install.sh
```

El instalador hace:
- Instala dependencias de sistema que Chromium necesita
- Crea usuario `woffu` y carpeta `/opt/woffu-bot`
- Crea venv e instala paquetes Python
- Descarga Chromium (~150 MB)

## Configurar

```bash
# Credenciales
sudo -u woffu cp /opt/woffu-bot/.env.example /opt/woffu-bot/.env
sudo -u woffu nano /opt/woffu-bot/.env
sudo -u woffu chmod 600 /opt/woffu-bot/.env

# Horario
sudo -u woffu nano /opt/woffu-bot/config.yaml
```

### Horario por día (`schedule:`)

```yaml
schedule:
  monday:
    - in:  "06:00"
      out: "12:00"
    - in:  "13:00"
      out: "15:30"
  friday:
    - in:  "07:00"
      out: "15:00"
  saturday: []   # día libre
```

### Variabilidad (`jitter:`)

```yaml
jitter:
  min_minutes: 2
  max_minutes: 3
```

Si la hora es `06:00`, el bot fichará entre `06:02:00` y `06:03:00`.
El jitter es **determinista por día**: se mantiene estable durante el
día (así un reinicio no te hace fichar dos veces a horas distintas).

### Exclusiones

```yaml
excluded_dates:
  single:
    - "2026-01-01"
  ranges:
    - start: "2026-08-01"
      end:   "2026-08-15"
      note:  "Vacaciones"
```

## Probar antes de "ir en serio"

```bash
# Ver fichajes programados de la semana
sudo -u woffu bash -lc 'cd /opt/woffu-bot && . venv/bin/activate && python main.py --week'

# Abrir el navegador, navegar al dashboard y verificar login + selectores
# (NO pulsa el botón final)
sudo -u woffu bash -lc 'cd /opt/woffu-bot && . venv/bin/activate && python main.py --test-browser'
```

Si `--test-browser` falla porque no encuentra el botón:
1. Mira la captura en `/opt/woffu-bot/screenshots/`
2. Inspecciona el HTML real del botón en tu Woffu
3. Añade el selector correcto en `config.yaml` → `browser.selectors.sign_button`

**Modo dry-run global**: en `config.yaml` pon `behavior.dry_run: true`
para que el bot navegue y haga login pero nunca pulse el botón. Déjalo
así un día entero y revisa los logs antes de poner `dry_run: false`.

## Activar como servicio

```bash
sudo cp /opt/woffu-bot/woffu-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now woffu-bot

# Estado y logs
sudo systemctl status woffu-bot
sudo journalctl -u woffu-bot -f
tail -f /opt/woffu-bot/logs/woffu.log
```

## Comandos útiles

```bash
# Fichar entrada ahora (manual)
python main.py --now in

# Fichar salida ahora
python main.py --now out

# Ver fichajes calculados para hoy
python main.py --show

# Probar navegador sin fichar
python main.py --test-browser
```

## Recursos en el VPS

- **CPU/RAM**: Chromium consume ~200–400 MB cuando arranca, pero el bot
  lo abre **solo durante el fichaje** (~5–15 segundos) y luego lo cierra.
  El resto del tiempo el proceso Python está dormido (~20 MB).
- **Disco**: Chromium ocupa ~300 MB en `~/.cache/ms-playwright/`.
  El perfil persistente otros ~10–20 MB.
- **Red**: insignificante (un par de páginas cada fichaje).

Cualquier VPS con 1 GB de RAM lo aguanta sin problema.

## Solución de problemas

| Síntoma | Posible causa / solución |
|---|---|
| `No se encontró el botón de fichaje` | Revisa el screenshot en `screenshots/`. Añade el selector correcto en `config.yaml`. |
| Login falla | Revisa `.env`. Si tu Woffu tiene 2FA, el bot no podrá entrar automáticamente; tendrías que iniciar sesión manualmente una vez con el navegador apuntando al `user_data_dir` y dejar marcada la opción "recordar dispositivo". |
| `chromium executable not found` | Ejecuta `python -m playwright install chromium` en el venv. |
| Errores `libnss3.so: cannot open shared object` | Faltan dependencias del sistema. Vuelve a ejecutar `install.sh` o instala los paquetes manualmente. |
| Funciona en local pero no en el VPS | Asegúrate de que `browser.headless: true` en config y que el VPS tenga las librerías gráficas (las instala `install.sh`). |

## Mantener viva la sesión

Como el perfil es persistente, después del primer login Woffu deja una
cookie de sesión que dura semanas. El bot sólo volverá a hacer el flujo
de login completo cuando esa cookie caduque. Esto es exactamente como se
comporta un humano que abre Woffu cada mañana.
