"""
Cliente de navegador para fichar en Woffu.

Usa Playwright (Chromium) con un perfil persistente y patrones de
comportamiento humano: movimientos de ratón con curvas, tipeo carácter a
carácter con micro-pausas, scrolls suaves, esperas variables, etc.

Incluye parches anti-detección (navigator.webdriver = false, plugins,
permissions, etc.) similares a playwright-stealth.
"""
from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from pathlib import Path
from typing import List, Optional

from playwright.async_api import (
    BrowserContext,
    Page,
    TimeoutError as PWTimeoutError,
    async_playwright,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Script anti-detección que se inyecta antes de cualquier JS de la página
# ---------------------------------------------------------------------------
STEALTH_INIT_SCRIPT = r"""
// 1) navigator.webdriver -> undefined
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

// 2) Plugins simulados (Chrome real reporta al menos algunos)
Object.defineProperty(navigator, 'plugins', {
  get: () => [
    { name: 'Chrome PDF Plugin' },
    { name: 'Chrome PDF Viewer' },
    { name: 'Native Client' },
  ],
});

// 3) Idiomas
Object.defineProperty(navigator, 'languages', { get: () => ['es-ES', 'es', 'en'] });

// 4) Permissions API: notifications no debe ser "denied" cuando webdriver
const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
if (originalQuery) {
  window.navigator.permissions.query = (parameters) =>
    parameters.name === 'notifications'
      ? Promise.resolve({ state: Notification.permission })
      : originalQuery(parameters);
}

// 5) Chrome runtime simulado
window.chrome = window.chrome || { runtime: {} };

// 6) WebGL vendor/renderer realistas
const getParameter = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function (parameter) {
  if (parameter === 37445) return 'Intel Inc.';
  if (parameter === 37446) return 'Intel Iris OpenGL Engine';
  return getParameter.call(this, parameter);
};
"""


class WoffuBrowserClient:
    def __init__(self, config: dict):
        self.config = config
        self.browser_cfg = config.get("browser", {}) or {}
        self.woffu_cfg = config.get("woffu", {}) or {}
        self.timezone = config.get("timezone", "Europe/Madrid")

        self.user = self.woffu_cfg.get("username") or ""
        self.password = self.woffu_cfg.get("password") or ""
        self.dashboard_url = self.woffu_cfg.get(
            "url", "https://grupoapex.woffu.com/v2/personal/dashboard/user?lang=es"
        )
        self.login_url = self.woffu_cfg.get("login_url", "https://grupoapex.woffu.com/")

        self.headless = bool(self.browser_cfg.get("headless", True))
        self.user_data_dir = Path(self.browser_cfg.get("user_data_dir", "browser_profile"))
        self.user_data_dir.mkdir(parents=True, exist_ok=True)

        self.user_agent = self.browser_cfg.get(
            "user_agent",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        )
        self.viewport = {
            "width": int(self.browser_cfg.get("viewport_width", 1366)),
            "height": int(self.browser_cfg.get("viewport_height", 768)),
        }
        self.locale = self.browser_cfg.get("locale", "es-ES")

        self.nav_timeout = int(self.browser_cfg.get("navigation_timeout_ms", 45000))
        self.act_timeout = int(self.browser_cfg.get("action_timeout_ms", 15000))

        sel = self.browser_cfg.get("selectors", {}) or {}
        self.sel_sign_button: List[str] = sel.get("sign_button", []) or []
        self.sel_username: List[str] = sel.get("username_field", []) or []
        self.sel_next: List[str] = sel.get("next_button", []) or []
        self.sel_password: List[str] = sel.get("password_field", []) or []
        self.sel_submit: List[str] = sel.get("submit_button", []) or []

        self.screenshot_on_error = bool(self.browser_cfg.get("screenshot_on_error", True))
        self.screenshot_dir = Path(self.browser_cfg.get("screenshot_dir", "screenshots"))
        if self.screenshot_on_error:
            self.screenshot_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Punto de entrada principal
    # ------------------------------------------------------------------
    async def perform_sign(self, action: str, dry_run: bool = False) -> bool:
        """
        Abre el navegador, navega al dashboard, hace login si hace falta,
        y pulsa el botón de fichaje. action es "in" u "out" (informativo).
        """
        assert action in ("in", "out")

        async with async_playwright() as pw:
            context = await pw.chromium.launch_persistent_context(
                user_data_dir=str(self.user_data_dir),
                headless=self.headless,
                user_agent=self.user_agent,
                viewport=self.viewport,
                locale=self.locale,
                timezone_id=self.timezone,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
            )
            await context.add_init_script(STEALTH_INIT_SCRIPT)
            context.set_default_navigation_timeout(self.nav_timeout)
            context.set_default_timeout(self.act_timeout)

            page = context.pages[0] if context.pages else await context.new_page()
            try:
                ok = await self._flow(page, action, dry_run)
                return ok
            except Exception as e:
                log.exception("Error durante el fichaje: %s", e)
                await self._screenshot(page, f"error_{action}")
                return False
            finally:
                # Pequeña espera para no parecer un robot que cierra al instante
                await self._human_sleep(1.0, 2.5)
                await context.close()

    # ------------------------------------------------------------------
    # Flujo principal
    # ------------------------------------------------------------------
    async def _flow(self, page: Page, action: str, dry_run: bool) -> bool:
        log.info("Navegando al dashboard…")
        await page.goto(self.dashboard_url, wait_until="domcontentloaded")
        await self._human_sleep(1.5, 3.0)

        # ¿Ha redirigido a login?
        if await self._looks_like_login(page):
            log.info("No hay sesión activa, haciendo login…")
            await self._do_login(page)
            await page.goto(self.dashboard_url, wait_until="domcontentloaded")
            await self._human_sleep(1.5, 3.0)

        # Cerrar popups/encuestas y paneles laterales antes de actuar
        await self._dismiss_popups(page)
        await self._close_panels(page)

        # Comportamiento humano: pequeño scroll y movimiento de ratón antes de actuar
        await self._humanize(page)

        # Cerrar panel si los movimientos de ratón lo reabrieron
        await self._close_panels(page)

        # Buscar el botón de fichaje
        button = await self._find_sign_button(page)
        if button is None:
            await self._screenshot(page, f"no_button_{action}")
            raise RuntimeError(
                "No se encontró el botón de fichaje. "
                "Revisa browser.selectors.sign_button en config.yaml "
                "(hay un screenshot en la carpeta screenshots/)."
            )

        if dry_run:
            text = (await button.inner_text()).strip()
            log.warning("DRY-RUN: encontrado botón '%s' pero NO se pulsa.", text)
            return True

        # Mover el ratón al botón con una curva humana y hacer clic
        await self._human_move_to(page, button)
        await self._human_sleep(0.3, 0.9)
        await button.click(delay=random.randint(60, 180))
        log.info("Botón de fichaje pulsado.")

        # Esperar respuesta y cerrar popup si aparece tras el click
        await self._human_sleep(2.0, 3.0)
        await self._dismiss_popups(page)
        await self._human_sleep(1.0, 2.0)

        # Captura "ok" para auditoría
        await self._screenshot(page, f"ok_{action}")
        return True

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------
    async def _looks_like_login(self, page: Page) -> bool:
        """Heurística: hay un campo de password visible."""
        for sel in self.sel_password:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    return True
            except Exception:
                pass
        # Backup: la URL contiene "login"
        return "login" in page.url.lower() or "signin" in page.url.lower()

    async def _do_login(self, page: Page) -> None:
        if not self.user or not self.password:
            raise RuntimeError(
                "Faltan credenciales (WOFFU_USERNAME/WOFFU_PASSWORD o config.yaml)"
            )

        if not await self._first_visible(page, self.sel_username):
            await page.goto(self.login_url, wait_until="networkidle")
            await self._human_sleep(1.5, 2.5)

        user_el = await self._first_visible(page, self.sel_username)
        if not user_el:
            await self._screenshot(page, "login_form_not_found")
            raise RuntimeError(
                "No se encontró el campo de usuario. "
                "Revisa browser.selectors.username_field"
            )

        await self._human_move_to(page, user_el)
        await user_el.click()
        await self._human_type(page, self.user)
        await self._human_sleep(0.4, 1.0)

        # Detectar si el login es de 2 pasos (email → Siguiente → contraseña)
        pwd_el = await self._first_visible(page, self.sel_password)
        if pwd_el is None:
            next_btn = await self._first_visible(page, self.sel_next)
            if next_btn:
                log.info("Login de 2 pasos detectado, pulsando 'Siguiente'…")
                await self._human_move_to(page, next_btn)
                await next_btn.click(delay=random.randint(60, 180))
                await self._human_sleep(1.5, 3.0)
                pwd_el = await self._first_visible(page, self.sel_password)
            else:
                await user_el.press("Enter")
                await self._human_sleep(1.5, 3.0)
                pwd_el = await self._first_visible(page, self.sel_password)

        if not pwd_el:
            await self._screenshot(page, "login_form_not_found")
            raise RuntimeError(
                "No se encontró el campo de contraseña tras el paso 1. "
                "Revisa browser.selectors.password_field"
            )

        await self._human_move_to(page, pwd_el)
        await pwd_el.click()
        await self._human_type(page, self.password)
        await self._human_sleep(0.4, 1.0)

        submit = await self._first_visible(page, self.sel_submit)
        if submit:
            await self._human_move_to(page, submit)
            await submit.click(delay=random.randint(60, 180))
        else:
            await pwd_el.press("Enter")

        try:
            await page.wait_for_load_state("networkidle", timeout=self.nav_timeout)
        except PWTimeoutError:
            log.warning("Timeout esperando networkidle tras login (puede ser normal)")

        await self._human_sleep(1.5, 3.0)

    # ------------------------------------------------------------------
    # Búsqueda del botón
    # ------------------------------------------------------------------
    async def _find_sign_button(self, page: Page):
        # Esperamos hasta 20s a que aparezca alguno.
        # Si hay varios botones con el mismo texto (panel + cronómetro),
        # elegimos el más cercano al centro horizontal de la pantalla.
        center_x = self.viewport["width"] / 2
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            candidates = []
            for sel in self.sel_sign_button:
                try:
                    els = await page.query_selector_all(sel)
                    for el in els:
                        if await el.is_visible() and await el.is_enabled():
                            box = await el.bounding_box()
                            if box:
                                btn_center_x = box["x"] + box["width"] / 2
                                dist = abs(btn_center_x - center_x)
                                candidates.append((dist, el))
                except Exception:
                    continue
            if candidates:
                candidates.sort(key=lambda c: c[0])
                return candidates[0][1]
            await asyncio.sleep(0.5)
        return None

    async def _first_visible(self, page: Page, selectors: List[str]):
        for sel in selectors:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    return el
            except Exception:
                continue
        return None

    # ------------------------------------------------------------------
    # Cierre de popups / encuestas
    # ------------------------------------------------------------------
    async def _dismiss_popups(self, page: Page) -> None:
        """Cierra cualquier modal o encuesta que bloquee la página."""
        close_selectors = [
            "button[aria-label='Close']",
            "button[aria-label='Cerrar']",
            "button.close",
            "[class*='modal'] button[class*='close']",
            "[class*='survey'] button[class*='close']",
            "button:has-text('×')",
            "button:has-text('Cerrar')",
            "button:has-text('No, gracias')",
            "button:has-text('Omitir')",
            ".modal-close",
        ]
        for sel in close_selectors:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    log.info("Cerrando popup: %s", sel)
                    await el.click()
                    await self._human_sleep(0.5, 1.0)
                    return
            except Exception:
                continue

    async def _close_panels(self, page: Page) -> None:
        """Cierra el panel de presencia/horario que aparece en la esquina superior derecha."""
        try:
            await page.keyboard.press("Escape")
            await self._human_sleep(0.3, 0.6)
            # Click en el título central de la página (zona segura, lejos del panel)
            await page.mouse.click(
                self.viewport["width"] // 2,
                self.viewport["height"] // 2 - 150,
            )
            await self._human_sleep(0.3, 0.6)
        except Exception as e:
            log.debug("close_panels ignorado: %s", e)

    # ------------------------------------------------------------------
    # Comportamiento humano
    # ------------------------------------------------------------------
    async def _humanize(self, page: Page) -> None:
        """Pequeños gestos antes de actuar: mover ratón, scroll suave."""
        try:
            # Movimiento aleatorio de ratón — evitar la esquina superior derecha
            # donde está el icono del panel de presencia
            safe_x_max = self.viewport["width"] - 300
            for _ in range(random.randint(1, 3)):
                x = random.randint(100, safe_x_max)
                y = random.randint(150, self.viewport["height"] - 200)
                await page.mouse.move(x, y, steps=random.randint(15, 30))
                await self._human_sleep(0.15, 0.5)

            # Scroll humano: varias rueditas pequeñas
            for _ in range(random.randint(0, 2)):
                await page.mouse.wheel(0, random.randint(80, 250))
                await self._human_sleep(0.2, 0.6)
        except Exception as e:
            log.debug("humanize ignorado: %s", e)

    async def _human_move_to(self, page: Page, element) -> None:
        """Mueve el ratón al centro del elemento con una pequeña curva."""
        try:
            box = await element.bounding_box()
            if not box:
                return
            target_x = box["x"] + box["width"] / 2 + random.uniform(-3, 3)
            target_y = box["y"] + box["height"] / 2 + random.uniform(-3, 3)
            # Punto intermedio "curvado"
            mid_x = target_x + random.uniform(-60, 60)
            mid_y = target_y + random.uniform(-30, 30)
            await page.mouse.move(mid_x, mid_y, steps=random.randint(10, 20))
            await self._human_sleep(0.05, 0.2)
            await page.mouse.move(target_x, target_y, steps=random.randint(8, 18))
        except Exception as e:
            log.debug("move_to ignorado: %s", e)

    async def _human_type(self, page: Page, text: str) -> None:
        """Teclea carácter a carácter con tiempo variable."""
        for ch in text:
            await page.keyboard.type(ch, delay=random.randint(40, 140))
            # alguna micro-pausa extra ocasional
            if random.random() < 0.06:
                await asyncio.sleep(random.uniform(0.1, 0.4))

    async def _human_sleep(self, lo: float, hi: float) -> None:
        await asyncio.sleep(random.uniform(lo, hi))

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------
    async def _screenshot(self, page: Page, name: str) -> None:
        if not self.screenshot_on_error:
            return
        try:
            ts = time.strftime("%Y%m%d-%H%M%S")
            path = self.screenshot_dir / f"{ts}_{name}.png"
            await page.screenshot(path=str(path), full_page=True)
            log.info("Screenshot guardado en %s", path)
        except Exception as e:
            log.debug("No se pudo guardar screenshot: %s", e)
