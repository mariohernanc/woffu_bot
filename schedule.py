"""
Gestión del horario configurado: parsea config.yaml y calcula
el "próximo fichaje" en cualquier momento dado.
"""
from __future__ import annotations

import logging
import os
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import List, Optional

import yaml
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

WEEKDAY_NAMES = [
    "monday", "tuesday", "wednesday", "thursday",
    "friday", "saturday", "sunday",
]


@dataclass
class ScheduledSign:
    when: datetime         # con tz
    action: str            # "in" | "out"
    base_time: str         # "HH:MM" original (para logging)

    def __str__(self) -> str:
        return f"{self.action.upper()} @ {self.when.strftime('%Y-%m-%d %H:%M:%S %Z')} (base {self.base_time})"


class Schedule:
    def __init__(self, config: dict):
        self.config = config
        tz_name = config.get("timezone", "Europe/Madrid")
        self.tz = ZoneInfo(tz_name)

        j = config.get("jitter", {}) or {}
        self.jitter_min = float(j.get("min_minutes", 0))
        self.jitter_max = float(j.get("max_minutes", 0))
        if self.jitter_max < self.jitter_min:
            self.jitter_max = self.jitter_min

        self.schedule = config.get("schedule", {}) or {}
        self.excluded = config.get("excluded_dates", {}) or {}

    @classmethod
    def load(cls, path: str) -> "Schedule":
        with open(path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        cfg.setdefault("woffu", {})
        if not cfg["woffu"].get("username"):
            cfg["woffu"]["username"] = os.environ.get("WOFFU_USERNAME", "")
        if not cfg["woffu"].get("password"):
            cfg["woffu"]["password"] = os.environ.get("WOFFU_PASSWORD", "")
        return cls(cfg)

    # ---- exclusiones ----
    def is_excluded(self, d: date) -> Optional[str]:
        single = self.excluded.get("single", []) or []
        for s in single:
            if str(s) == d.isoformat():
                return f"fecha excluida ({s})"
        ranges = self.excluded.get("ranges", []) or []
        for rg in ranges:
            try:
                start = date.fromisoformat(str(rg["start"]))
                end = date.fromisoformat(str(rg["end"]))
            except Exception:
                continue
            if start <= d <= end:
                return f"rango excluido ({start}…{end}) {rg.get('note', '')}".strip()
        return None

    # ---- horario por día ----
    def signs_for_day(self, d: date) -> List[ScheduledSign]:
        reason = self.is_excluded(d)
        if reason:
            log.debug("%s excluida: %s", d, reason)
            return []

        weekday = WEEKDAY_NAMES[d.weekday()]
        pairs = self.schedule.get(weekday, []) or []
        if not pairs:
            return []

        out: List[ScheduledSign] = []
        prev_when: Optional[datetime] = None
        idx = 0
        for pair in pairs:
            for key in ("in", "out"):
                if key not in pair:
                    continue
                hh, mm = map(int, pair[key].split(":"))
                base = datetime(d.year, d.month, d.day, hh, mm, tzinfo=self.tz)
                jitter_seconds = self._deterministic_jitter_seconds(d, idx)
                when = base + timedelta(seconds=jitter_seconds)
                if prev_when and when <= prev_when:
                    when = prev_when + timedelta(seconds=30)
                out.append(ScheduledSign(when=when, action=key, base_time=pair[key]))
                prev_when = when
                idx += 1
        return out

    def _deterministic_jitter_seconds(self, d: date, idx: int) -> float:
        if self.jitter_min == 0 and self.jitter_max == 0:
            return 0.0
        seed = f"{d.isoformat()}#{idx}"
        rnd = random.Random(seed)
        # Minutos + segundos aleatorios para más realismo
        minutes = rnd.uniform(self.jitter_min, self.jitter_max)
        return minutes * 60.0

    def next_pending(self, now: datetime, catch_up_window_minutes: int = 0) -> Optional[ScheduledSign]:
        if now.tzinfo is None:
            now = now.replace(tzinfo=self.tz)
        else:
            now = now.astimezone(self.tz)

        for offset in (0, 1):
            day = (now + timedelta(days=offset)).date()
            for s in self.signs_for_day(day):
                if now <= s.when:
                    return s
                if now <= s.when + timedelta(minutes=catch_up_window_minutes):
                    return s
        return None
