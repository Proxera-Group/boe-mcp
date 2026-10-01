"""Cliente de pruebas: sirve los fixtures grabados mediante httpx.MockTransport (sin red)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from boe_mcp import server
from boe_mcp.client import BoeClient

FIX = Path(__file__).parent / "fixtures"


def fx(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def resp(status: int, body: str, ctype: str = "application/json") -> httpx.Response:
    return httpx.Response(status, text=body, headers={"content-type": ctype})


class FakeBoe:
    """Enruta las URL de boe.es a fixtures y registra las peticiones."""

    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self.search_queries: list[dict] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        u = urlparse(str(req.url))
        path, qs = u.path, parse_qs(u.query)

        assert req.headers["user-agent"].startswith("boe-mcp (+https://proxera.es)"), "User-Agent no identificable"

        if m := re.fullmatch(r"/datosabiertos/api/boe/sumario/(\w+)", path):
            f = m.group(1)
            if not f.isdigit():
                return resp(400, fx("sumario_400_fecha.xml"), "application/xml")
            try:
                return resp(200, fx(f"sumario_{f}.json"))
            except FileNotFoundError:
                return resp(404, fx("sumario_404_domingo.xml"), "application/xml")

        if path == "/datosabiertos/api/datos-auxiliares/materias":
            return resp(200, fx("materias_subset.json"))

        if path == "/datosabiertos/api/legislacion-consolidada":
            q = json.loads(qs["query"][0])
            self.search_queries.append({"query": q, "limit": qs.get("limit")})
            qstr = q["query"].get("query_string", {}).get("query", "")
            if "campo_malo" in qstr:
                return resp(500, fx("legislacion_500.xml"), "application/xml")
            if "zzzxxqq" in qstr:
                return resp(200, fx("legislacion_vacia.json"))
            if "27/2026" in qstr:
                return resp(200, fx("legislacion_rdl27.json"))
            return resp(200, fx("legislacion_iva.json"))

        if m := re.fullmatch(r"/datosabiertos/api/legislacion-consolidada/id/([\w-]+)(/.*)?", path):
            ident, rest = m.group(1), m.group(2) or ""
            if ident == "BOE-A-1992-28740":
                if rest == "/metadatos":
                    return resp(200, fx("metadatos_iva.json"))
                if rest == "/texto/indice":
                    return resp(200, fx("indice_iva.json"))
                if rest == "/texto/bloque/a1":
                    return resp(200, fx("bloque_iva_a1.xml"), "application/xml")
                if rest == "/analisis":
                    return resp(200, '{"status":{"code":"200","text":"ok"},"data":[{}]}')
                if rest.startswith("/texto/bloque/"):
                    return resp(404, fx("consolidada_404.xml"), "application/xml")
            if ident == "BOE-A-2026-20385":
                if rest == "/metadatos":
                    return resp(200, fx("legislacion_rdl27.json"))  # misma forma que metadatos
                if rest == "/texto/indice":
                    return resp(200, fx("indice_rdl27.json"))
                if rest == "/texto":
                    return resp(200, fx("texto_rdl27.xml"), "application/xml")
                if rest == "/analisis":
                    return resp(200, fx("analisis_rdl27.json"))
            return resp(404, fx("consolidada_404.xml"), "application/xml")

        if path == "/diario_boe/xml.php":
            ident = qs["id"][0]
            if ident == "BOE-A-2026-20384":
                return resp(200, fx("documento_BOE-A-2026-20384.xml"), "application/xml")
            return resp(400, "<!DOCTYPE html><html><body>Error</body></html>", "text/html")

        return resp(404, "no mock para " + str(req.url), "text/plain")


@pytest.fixture
def fake():
    return FakeBoe()


@pytest.fixture
def client(fake, tmp_path):
    async def nosleep(_):  # los tests no esperan
        return None

    c = BoeClient(transport=httpx.MockTransport(fake), cache_dir=None, min_interval=0, sleep=nosleep)
    server._vocab_materias = None
    return c


@pytest.fixture(autouse=True)
def sin_red(monkeypatch):
    """Cualquier intento de abrir un socket hace fallar el test."""
    import socket

    def prohibido(*a, **k):
        raise RuntimeError("los tests no pueden usar la red")

    monkeypatch.setattr(socket.socket, "connect", prohibido)
    monkeypatch.setattr(socket, "create_connection", prohibido)
