"""Cliente HTTP mínimo y respetuoso para boe.es.

- User-Agent identificable.
- Timeouts explícitos.
- Reintentos suaves (429/5xx/timeouts) con espera creciente.
- Pausa mínima entre peticiones (sin ráfagas).
- Caché en disco opcional (variable BOE_MCP_CACHE_DIR).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

from . import __version__

BASE = "https://www.boe.es"
API = f"{BASE}/datosabiertos/api"
USER_AGENT = f"boe-mcp (+https://proxera.es) v{__version__}"

# Pausa mínima entre dos peticiones a boe.es (segundos).
MIN_INTERVAL = float(os.environ.get("BOE_MCP_MIN_INTERVAL", "0.4"))
RETRIES = 2  # reintentos tras el primer intento
TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class BoeError(Exception):
    """Error presentable al usuario (el mensaje va en castellano)."""


class BoeNotFound(BoeError):
    """La API respondió 404: el recurso no existe."""


class BoeClient:
    def __init__(
        self,
        transport: httpx.AsyncBaseTransport | None = None,
        cache_dir: str | os.PathLike | None = None,
        min_interval: float | None = None,
        sleep=asyncio.sleep,
    ) -> None:
        self._client = httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
            follow_redirects=True,
            transport=transport,
        )
        env_dir = os.environ.get("BOE_MCP_CACHE_DIR")
        chosen = cache_dir if cache_dir is not None else env_dir
        self._cache_dir = Path(chosen).expanduser() if chosen else None
        self._min_interval = MIN_INTERVAL if min_interval is None else min_interval
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- caché ---------------------------------------------------------
    def _cache_path(self, key: str) -> Path | None:
        if not self._cache_dir:
            return None
        return self._cache_dir / (hashlib.sha256(key.encode()).hexdigest() + ".json")

    def _cache_get(self, key: str, ttl: float) -> str | None:
        path = self._cache_path(key)
        if not path or ttl <= 0:
            return None
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
            if time.time() - entry["t"] <= ttl:
                return entry["body"]
        except (OSError, ValueError, KeyError):
            pass
        return None

    def _cache_put(self, key: str, body: str) -> None:
        path = self._cache_path(key)
        if not path:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"t": time.time(), "body": body}), encoding="utf-8")
            tmp.replace(path)
        except OSError:
            pass  # la caché nunca debe romper una consulta

    # -- peticiones ----------------------------------------------------
    async def get_text(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        accept: str = "application/json",
        ttl: float = 0,
    ) -> str:
        """GET con reintentos y throttle. Devuelve el cuerpo como texto.

        404 -> BoeNotFound; otros errores 4xx/5xx -> BoeError. `ttl` (segundos)
        activa la caché en disco para esta petición si hay directorio configurado.
        """
        key = f"{accept}|{url}|{json.dumps(params, sort_keys=True, ensure_ascii=False)}"
        cached = self._cache_get(key, ttl)
        if cached is not None:
            return cached

        last_exc: Exception | None = None
        for attempt in range(RETRIES + 1):
            if attempt:
                await self._sleep(1.5 * attempt)
            async with self._lock:
                wait = self._min_interval - (time.monotonic() - self._last)
                if wait > 0:
                    await self._sleep(wait)
                try:
                    resp = await self._client.get(url, params=params, headers={"Accept": accept})
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    last_exc = exc
                    self._last = time.monotonic()
                    continue
                self._last = time.monotonic()

            if resp.status_code == 404:
                raise BoeNotFound("boe.es indica que la información solicitada no existe (404).")
            if resp.status_code == 429 or resp.status_code >= 500:
                last_exc = BoeError(f"boe.es respondió {resp.status_code}")
                if resp.status_code == 500 and "Code: 101" in resp.text:
                    # error de sintaxis de la consulta: reintentar no sirve
                    raise BoeError("boe.es rechazó la consulta (error 500, código 101: consulta mal formada).")
                continue
            if resp.status_code >= 400:
                raise BoeError(f"boe.es rechazó la petición ({resp.status_code}): {_status_text(resp.text)}")
            text = resp.text
            self._cache_put(key, text)
            return text

        raise BoeError(f"No se pudo contactar con boe.es tras {RETRIES + 1} intentos ({last_exc}).")

    async def get_json(self, url: str, params: dict[str, Any] | None = None, ttl: float = 0) -> Any:
        text = await self.get_text(url, params, accept="application/json", ttl=ttl)
        try:
            return json.loads(text)
        except ValueError as exc:
            raise BoeError("boe.es devolvió una respuesta que no es JSON válido.") from exc


def _status_text(body: str) -> str:
    """Extrae <text> del XML de error de la API."""
    import re

    m = re.search(r"<text>(.*?)</text>", body, re.S)
    return m.group(1).strip() if m else "petición no válida (¿identificador o fecha incorrectos?)"
