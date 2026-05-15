"""
Bot de fichaje Woffu (modo navegador).

Uso normal:
  python main.py                  # bucle infinito

Comandos auxiliares:
  python main.py --show           # fichajes de hoy
  python main.py --week           # fichajes de los próximos 7 días
  python main.py --now in         # fichar entrada ahora mismo
  python main.py --now out        # fichar salida ahora mismo
  python main.py --test-browser   # abre el navegador y prueba login + dashboard
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import logging.handlers
import os
import signal
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from zoneinfo import ZoneInfo

from schedule import Schedule, ScheduledSign
from woffu_browser import WoffuBrowserClient

log = logging.getLogger("woffu-bot")


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
def setup_logging(cfg: dict) -> None:
    log_cfg = cfg.get("logging", {}) or {}
    level = getattr(logging, str(log_cfg.get("level", "INFO")).upper(), logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    log_file = log_cfg.get("file")
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=int(log_cfg.get("max_size_mb", 5)) * 1024 * 1024,
            backupCount=int(log_cfg.get("backup_count", 5)),
            encoding="utf-8",
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)


# ---------------------------------------------------------------------------
# Comandos
# ---------------------------------------------------------------------------
def cmd_show(sched_obj: Schedule, week: bool = False) -> None:
    now = datetime.now(sched_obj.tz)
    days = 7 if week else 1
    for i in range(days):
        d = now + timedelta(days=i)
        signs = sched_obj.signs_for_day(d.date())
        header = d.strftime("%A %Y-%m-%d")
        reason = sched_obj.is_excluded(d.date())
        if reason:
            print(f"\n{header}: SIN FICHAJES ({reason})")
            continue
        if not signs:
            print(f"\n{header}: sin fichajes configurados")
            continue
        print(f"\n{header}:")
        for s in signs:
            marker = "→" if s.action == "in" else "←"
            print(f"  {marker} {s.action.upper():3s} a las {s.when.strftime('%H:%M:%S')}"
                  f"   (base {s.base_time})")


async def cmd_now_async(cfg: dict, action: str) -> int:
    client = WoffuBrowserClient(cfg)
    dry_run = bool(cfg.get("behavior", {}).get("dry_run", False))
    behavior = cfg.get("behavior", {}) or {}
    max_retries = int(behavior.get("max_retries", 3))
    retry_delay = int(behavior.get("retry_delay_seconds", 30))
    for attempt in range(1, max_retries + 1):
        log.info("Intento %d/%d: fichando %s", attempt, max_retries, action.upper())
        ok = await client.perform_sign(action, dry_run=dry_run)
        if ok:
            return 0
        if attempt < max_retries:
            log.warning("Reintentando en %ds…", retry_delay)
            await asyncio.sleep(retry_delay)
    return 1


async def cmd_test_browser_async(cfg: dict) -> int:
    """Abre el navegador, navega al dashboard (haciendo login si hace falta)
    pero NO pulsa el botón. Útil para depurar selectores y credenciales."""
    cfg.setdefault("behavior", {})["dry_run"] = True
    client = WoffuBrowserClient(cfg)
    ok = await client.perform_sign("in", dry_run=True)
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# Bucle principal
# ---------------------------------------------------------------------------
async def run_loop_async(cfg: dict, sched_obj: Schedule) -> int:
    behavior = cfg.get("behavior", {}) or {}
    catch_up = int(behavior.get("catch_up_window_minutes", 10))
    dry_run = bool(behavior.get("dry_run", False))
    max_retries = int(behavior.get("max_retries", 3))
    retry_delay = int(behavior.get("retry_delay_seconds", 30))

    stop_event = asyncio.Event()

    def handle_sig():
        log.info("Señal recibida, terminando…")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, handle_sig)
        except NotImplementedError:
            pass  # Windows

    log.info("Bot iniciado. tz=%s  dry_run=%s  catch_up=%dmin",
             sched_obj.tz, dry_run, catch_up)

    while not stop_event.is_set():
        now = datetime.now(sched_obj.tz)
        nxt = sched_obj.next_pending(now, catch_up_window_minutes=catch_up)

        if nxt is None:
            tomorrow = (now + timedelta(days=1)).replace(
                hour=0, minute=5, second=0, microsecond=0
            )
            log.info("Sin fichajes próximos. Durmiendo hasta %s",
                     tomorrow.strftime("%Y-%m-%d %H:%M:%S"))
            await _wait_until(tomorrow, stop_event)
            continue

        log.info("Próximo fichaje: %s", nxt)

        if now < nxt.when:
            await _wait_until(nxt.when, stop_event)

        if stop_event.is_set():
            break

        client = WoffuBrowserClient(cfg)
        ok = False
        for attempt in range(1, max_retries + 1):
            log.info("Ejecutando fichaje %s (intento %d/%d)",
                     nxt.action.upper(), attempt, max_retries)
            try:
                ok = await client.perform_sign(nxt.action, dry_run=dry_run)
                if ok:
                    log.info("Fichaje OK")
                    break
            except Exception as e:
                log.exception("Excepción en intento %d: %s", attempt, e)
            if attempt < max_retries:
                await asyncio.sleep(retry_delay)
        if not ok:
            log.error("Fichaje %s ABORTADO tras %d intentos", nxt, max_retries)

        # Pausa para que el "siguiente pendiente" pase al siguiente slot
        await asyncio.sleep(5)

    log.info("Bot detenido.")
    return 0


async def _wait_until(target: datetime, stop_event: asyncio.Event) -> None:
    """Espera hasta target.tzinfo aware, pero abandona si stop_event se activa."""
    while not stop_event.is_set():
        now = datetime.now(target.tzinfo)
        remaining = (target - now).total_seconds()
        if remaining <= 0:
            return
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=min(remaining, 30))
        except asyncio.TimeoutError:
            continue


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(description="Bot de fichaje Woffu (navegador)")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--show", action="store_true",
                        help="Mostrar fichajes programados de hoy y salir")
    parser.add_argument("--week", action="store_true",
                        help="Mostrar fichajes de los próximos 7 días y salir")
    parser.add_argument("--now", choices=["in", "out"],
                        help="Fichar ahora mismo (in u out) y salir")
    parser.add_argument("--test-browser", action="store_true",
                        help="Probar navegador y selectores (sin pulsar el botón)")
    args = parser.parse_args()

    sched_obj = Schedule.load(args.config)
    setup_logging(sched_obj.config)

    if args.show or args.week:
        cmd_show(sched_obj, week=args.week)
        return 0

    if args.test_browser:
        return asyncio.run(cmd_test_browser_async(sched_obj.config))

    if args.now:
        return asyncio.run(cmd_now_async(sched_obj.config, args.now))

    return asyncio.run(run_loop_async(sched_obj.config, sched_obj))


if __name__ == "__main__":
    sys.exit(main())
