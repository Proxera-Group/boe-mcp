from __future__ import annotations

import asyncio
import datetime as dt
import json

import httpx
import pytest

from boe_mcp import parsing as P
from boe_mcp import server as S
from boe_mcp.client import BoeClient, BoeError
from boe_mcp.perfiles import coincidencias, resolver_perfiles

from conftest import FIX, fx

run = asyncio.run
HOY = dt.date(2026, 10, 1)  # jueves; fecha de captura de los fixtures


# ---------------------------------------------------------------- fechas
def test_parse_fecha():
    assert P.parse_fecha("hoy", HOY) == HOY
    assert P.parse_fecha("ayer", HOY) == dt.date(2026, 9, 30)
    assert P.parse_fecha("2026-09-30") == dt.date(2026, 9, 30)
    assert P.parse_fecha("20260930") == dt.date(2026, 9, 30)
    assert P.parse_fecha("30/09/2026") == dt.date(2026, 9, 30)
    with pytest.raises(BoeError):
        P.parse_fecha("mañana por la tarde")


# --------------------------------------------------------------- sumario
def test_normalize_sumario_real():
    s = P.normalize_sumario(json.loads(fx("sumario_20260930.json")))
    assert s["fecha"] == "2026-09-30"
    assert {b["numero"] for b in s["boletines"]} == {"241", "242"}
    items = [i for sec in s["secciones"] for d in sec["departamentos"] for i in d["disposiciones"]]
    assert len(items) == 213  # nº de <item> del JSON real
    rd = next(i for i in items if i["id"] == "BOE-A-2026-20384")
    assert rd["titulo"].startswith("Real Decreto 795/2026")
    assert rd["url_html"] == "https://www.boe.es/diario_boe/txt.php?id=BOE-A-2026-20384"
    assert rd["url_pdf"].endswith("BOE-A-2026-20384.pdf")
    assert rd["epigrafe"] == "Situación de interés para la seguridad nacional"
    assert all(i["id"] and i["titulo"] for i in items)


def test_sumario_omite_anuncios_por_defecto(client):
    out = run(S.sumario_core(client, "2026-09-30", hoy=HOY))
    assert out["anuncios_omitidos"] > 0
    assert not any(str(s["codigo"]).startswith("5") for s in out["secciones"])
    full = run(S.sumario_core(client, "2026-09-30", incluir_anuncios=True, hoy=HOY))
    assert full["total_disposiciones"] == 213
    assert full["total_disposiciones"] == out["total_disposiciones"] + out["anuncios_omitidos"]


def test_sumario_filtro_sin_tildes(client):
    out = run(S.sumario_core(client, "2026-09-30", filtro="SEGURIDAD NACIONAL", hoy=HOY))
    ids = [i["id"] for s in out["secciones"] for d in s["departamentos"] for i in d["disposiciones"]]
    assert "BOE-A-2026-20384" in ids


def test_sumario_domingo_y_futuro(client):
    with pytest.raises(BoeError, match="domingos"):
        run(S.sumario_core(client, "2026-09-27", hoy=HOY))
    with pytest.raises(BoeError, match="futura"):
        run(S.sumario_core(client, "2026-10-05", hoy=HOY))


# ------------------------------------------------------------ legislación
def test_build_query_sanea_y_range_completo():
    q = P.build_query('IVA "factura" and (x) or ¿?', desde=dt.date(2026, 1, 1))
    qs = q["query"]["query_string"]["query"]
    assert qs == "titulo:(IVA and factura and x)"
    assert P.build_query("IVA", donde="texto")["query"]["query_string"]["query"] == "texto:(IVA)"
    assert P.build_query("IVA", donde="ambos")["query"]["query_string"]["query"] == "(titulo:(IVA) or texto:(IVA))"
    # La API ignora range si falta gte o lte: siempre van los dos.
    assert q["query"]["range"]["fecha_publicacion"] == {"gte": "20260101", "lte": "29991231"}
    with pytest.raises(BoeError):
        P.build_query("")
    # solo fechas, sin texto: no debe enviarse query_string vacía (la API da 500)
    q2 = P.build_query("", hasta=dt.date(2000, 1, 1))
    assert "query_string" not in q2["query"]


def test_buscar_legislacion(client, fake):
    out = run(S.buscar_core(client, "impuesto valor añadido", limite=3))
    r = out["resultados"][0]
    assert r["id"] == "BOE-A-1992-28740"
    assert r["rango"] == "Ley"
    assert r["fecha_disposicion"] == "1992-12-28"
    assert r["vigencia"] == "vigente"
    assert r["url"] == "https://www.boe.es/buscar/act.php?id=BOE-A-1992-28740"
    assert out["total_devueltos"] == 3 and "aviso" in out and out["buscado_en"] == "titulo"
    assert fake.search_queries[0]["limit"] == ["3"]


def test_buscar_auto_cae_a_texto_si_titulo_vacio(client, fake):
    out = run(S.buscar_core(client, "zzzxxqq"))
    campos = [q["query"]["query"]["query_string"]["query"] for q in fake.search_queries]
    assert campos == ["titulo:(zzzxxqq)", "texto:(zzzxxqq)"]
    assert out["buscado_en"] == "texto"
    run(S.buscar_core(client, "impuesto valor añadido", donde="titulo"))
    assert len(fake.search_queries) == 3  # con resultados en título no se repite


def test_buscar_donde_invalido(client):
    with pytest.raises(BoeError, match="donde"):
        run(S.buscar_core(client, "iva", donde="nube"))


def test_buscar_sin_resultados_data_vacia(client):
    out = run(S.buscar_core(client, "zzzxxqq"))
    assert out["resultados"] == [] and out["total_devueltos"] == 0


def test_buscar_materia_y_rango(client, fake):
    run(S.buscar_core(client, "", materia="arrendamientos urbanos", rango="Real Decreto-ley"))
    q = fake.search_queries[-1]["query"]["query"]["query_string"]["query"]
    assert "materia@codigo:(305)" in q and "rango@codigo:1320" in q


def test_materia_ambigua_o_inexistente():
    voc = json.loads(fx("materias_subset.json"))["data"]
    assert P.resolver_materia("Impuesto sobre Sociedades", voc) == [4113]
    assert P.resolver_materia("impuesto sobre el valor añadido", voc) == [4102]  # sin tildes/mayúsculas
    assert P.resolver_materia("Viviendas", voc) == [7162]
    assert P.resolver_materia("4113", voc) == [4113]  # también acepta el código
    # varias parciales (organismos incluidos): no se mezclan, se pide concretar
    with pytest.raises(BoeError, match="parecidas") as e:
        P.resolver_materia("protección de datos", voc)
    assert "Agencia" in str(e.value)
    assert P.resolver_materia("arrendamientos", voc) == [303]  # exacta gana a las parciales
    with pytest.raises(BoeError, match="No encuentro"):
        P.resolver_materia("astrología", voc)


def test_error_500_de_la_api_no_se_reintenta(client, fake):
    with pytest.raises(BoeError, match="mal formada"):
        run(client.get_text("https://www.boe.es/datosabiertos/api/legislacion-consolidada",
                            {"query": json.dumps({"query": {"query_string": {"query": "campo_malo:(x"}}})}))
    assert len(fake.calls) == 1


# ------------------------------------------------------------------ norma
def test_leer_norma_larga_devuelve_indice(client):
    out = run(S.norma_core(client, "BOE-A-1992-28740", hoy=HOY))
    assert out["titulo"].startswith("Ley 37/1992")
    assert "texto" not in out and "aviso" in out
    assert len(out["indice"]) == 307
    assert out["indice"][3].startswith("a1 · Artículo 1")


def test_leer_norma_bloque(client):
    out = run(S.norma_core(client, "BOE-A-1992-28740", bloque="a1", hoy=HOY))
    assert out["bloques_incluidos"] == ["a1"]
    assert "Artículo 1. Naturaleza del impuesto." in out["texto"]
    assert "c) Las importaciones de bienes." in out["texto"]


def test_leer_norma_bloque_inexistente(client):
    with pytest.raises(BoeError, match="inexistentes"):
        run(S.norma_core(client, "BOE-A-1992-28740", bloque="zz99", hoy=HOY))


def test_leer_norma_corta_texto_completo_y_referencias(client):
    out = run(S.norma_core(client, "BOE-A-2026-20385", hoy=dt.date(2026, 10, 2)))
    assert "Real Decreto-ley 27/2026" in out["titulo"]
    assert out["materias"] == ["Viviendas"]
    assert any(r.startswith("MODIFICA el art. 10 de la Ley 29/1994") for r in out["modifica_o_cita"])
    assert "Artículo único" in out["texto"] and "Disposición final" in out["texto"]
    assert "pr" in out["bloques_incluidos"]


def test_leer_norma_trunca(client):
    out = run(S.norma_core(client, "BOE-A-2026-20385", max_caracteres=1000, hoy=dt.date(2026, 10, 2)))
    assert out["truncado"] is True and len(out["texto"]) == 1000


def test_leer_norma_id_invalido_e_inexistente(client):
    with pytest.raises(BoeError, match="Identificador no válido"):
        run(S.norma_core(client, "../../etc/passwd", hoy=HOY))
    with pytest.raises(BoeError, match="no está en la legislación consolidada"):
        run(S.norma_core(client, "BOE-A-1992-99999", hoy=HOY))


def test_bloques_solo_ultima_version():
    xml = """<response><data><bloque id="a1" tipo="precepto" titulo="Artículo 1">
      <version id_norma="X" fecha_publicacion="20000101" fecha_vigencia="20000102"><p>vieja</p></version>
      <version id_norma="Y" fecha_publicacion="20100101" fecha_vigencia="20100102"><p>nueva</p>
        <table><tr><td>a</td><td>b</td></tr></table><img src="data:..."/></version>
    </bloque></data></response>"""
    (b,) = P.parse_bloques(xml, dt.date(2026, 1, 1))
    assert b["versiones"] == 2 and b["vigente_desde"] == "2010-01-02" and b["norma_origen"] == "Y"
    assert "nueva" in b["texto"] and "vieja" not in b["texto"]
    assert "| a | b |" in b["texto"] and "[imagen omitida]" in b["texto"]


def test_version_futura_no_se_presenta_como_vigente():
    """Un bloque con una modificación publicada pero aún no en vigor: vigente = la de hoy."""
    xml = """<response><data><bloque id="a10" tipo="precepto" titulo="Artículo 10">
      <version id_norma="A" fecha_publicacion="19941125" fecha_vigencia="19950101"><p>texto antiguo</p></version>
      <version id_norma="B" fecha_publicacion="20261001" fecha_vigencia="20261002"><p>texto nuevo</p></version>
    </bloque></data></response>"""
    (b,) = P.parse_bloques(xml, dt.date(2026, 10, 1))
    assert b["texto"] == "texto antiguo" and not b["aun_no_en_vigor"]
    assert b["proxima"]["desde"] == "2026-10-02" and b["proxima"]["texto"] == "texto nuevo"
    (b,) = P.parse_bloques(xml, dt.date(2026, 10, 2))  # ya en vigor
    assert b["texto"] == "texto nuevo" and b["proxima"] is None


def test_norma_aun_no_en_vigor_se_avisa(client):
    # RDL 27/2026: todo su texto entra en vigor el 2-oct; el 1-oct no debe darse por vigente
    out = run(S.norma_core(client, "BOE-A-2026-20385", hoy=HOY))
    assert "AÚN NO EN VIGOR: entra en vigor el 2026-10-02" in out["texto"]
    assert "aviso_vigencia" in out
    hoy_ok = run(S.norma_core(client, "BOE-A-2026-20385", hoy=dt.date(2026, 10, 2)))
    assert "AÚN NO EN VIGOR" not in hoy_ok["texto"] and "aviso_vigencia" not in hoy_ok


def test_referencias_largas_se_recortan():
    refs = [{"anterior": [{"id_norma": f"BOE-A-2000-{i}", "relacion": {"texto": "CITA"}, "texto": "x"} for i in range(40)]}]
    a = P.parse_analisis({"data": [{"referencias": {"anteriores": refs}}]})
    assert len(a["modifica_o_cita"]) == 26 and a["modifica_o_cita"][-1].startswith("… y 15 referencias más")


# -------------------------------------------------------------- documento
def test_leer_documento(client):
    out = run(S.documento_core(client, "BOE-A-2026-20384"))
    assert out["titulo"].startswith("Real Decreto 795/2026")
    assert out["rango"] == "Real Decreto" and out["fecha_publicacion"] == "2026-09-30"
    assert "Ceuta" in out["materias"]
    assert any("MODIFICA el anexo del Real Decreto 705/2026" in r for r in out["referencias"])
    assert "Disposición final única." in out["texto"]
    assert out["texto"].rstrip().endswith("PEDRO SÁNCHEZ PÉREZ-CASTEJÓN")
    assert out["url_html"].endswith("txt.php?id=BOE-A-2026-20384")


def test_leer_documento_paginacion(client):
    a = run(S.documento_core(client, "BOE-A-2026-20384", max_caracteres=1000))
    assert a["truncado"] and len(a["texto"]) == 1000
    b = run(S.documento_core(client, "BOE-A-2026-20384", inicio=1000, max_caracteres=1000))
    assert b["texto"] != a["texto"]
    total = run(S.documento_core(client, "BOE-A-2026-20384", max_caracteres=200_000))["texto"]
    assert total[:1000] == a["texto"] and total[1000:2000] == b["texto"]


def test_leer_documento_inexistente(client):
    with pytest.raises(BoeError, match="No se pudo obtener"):
        run(S.documento_core(client, "BOE-A-2026-9999999"))


# -------------------------------------------------------------- novedades
def test_perfiles_coincidencias():
    act, _ = resolver_perfiles(["IVA", "Alquiler"])
    assert set(act) == {"fiscal", "vivienda"}
    assert coincidencias("Orden por la que se aprueba el modelo 303 del Impuesto sobre el Valor Añadido", act) == ["fiscal"]
    assert coincidencias("Real Decreto-ley 27/2026 … contratos de arrendamiento de vivienda habitual", act) == ["vivienda"]
    assert coincidencias("Resolución sobre subvenciones a la acuicultura", act) == []
    libre, _ = resolver_perfiles(["Ceuta"])
    assert coincidencias("Situación … en la ciudad de Ceuta", libre) == ["«Ceuta»"]


def test_novedades_vivienda_y_fiscal(client):
    out = run(S.novedades_core(client, ["vivienda", "fiscal"], dias=3, hoy=HOY))
    assert out["periodo"] == "2026-09-29 a 2026-10-01"
    ids = {r["id"] for r in out["resultados"]}
    assert "BOE-A-2026-20385" in ids  # RDL 27/2026, alquiler (publicado el 1-oct)
    rdl = next(r for r in out["resultados"] if r["id"] == "BOE-A-2026-20385")
    assert rdl["fecha"] == "2026-10-01" and "vivienda" in rdl["coincide_con"]
    fechas = [r["fecha"] for r in out["resultados"]]
    assert fechas == sorted(fechas, reverse=True)  # lo más reciente primero
    assert out["total_coincidencias"] >= len(out["resultados"]) > 1


def test_novedades_salta_domingos_y_dias_sin_boletin(client, fake):
    # 28/09 no tiene fixture (-> 404 simulado) y el 27/09 es domingo (ni se pide)
    out = run(S.novedades_core(client, ["fiscal"], dias=5, hoy=HOY))
    assert out["dias_sin_boletin"] == ["2026-09-28"]
    pedidos = {c.url.path.rsplit("/", 1)[1] for c in fake.calls}
    assert "20260927" not in pedidos


def test_novedades_excluye_personal_y_anuncios(client):
    out = run(S.novedades_core(client, ["Ministerio"], dias=1, hoy=HOY))
    assert all(not r["seccion"].startswith(("II.", "V.")) for r in out["resultados"])


def test_novedades_limite_y_validacion(client):
    out = run(S.novedades_core(client, ["laboral"], dias=3, limite=2, hoy=HOY))
    assert len(out["resultados"]) <= 2
    with pytest.raises(BoeError):
        run(S.novedades_core(client, [], hoy=HOY))


# ------------------------------------------------------------ cliente HTTP
def test_reintentos_suaves_y_agotamiento():
    estados = iter([503, 429, 200])

    def handler(req):
        s = next(estados)
        return httpx.Response(s, text='{"ok":1}' if s == 200 else "x")

    slept = []

    async def sleep(t):
        slept.append(t)

    c = BoeClient(transport=httpx.MockTransport(handler), min_interval=0, sleep=sleep)
    assert run(c.get_json("https://www.boe.es/x")) == {"ok": 1}
    assert slept == [1.5, 3.0]  # espera creciente

    c2 = BoeClient(transport=httpx.MockTransport(lambda r: httpx.Response(503)), min_interval=0, sleep=sleep)
    with pytest.raises(BoeError, match="tras 3 intentos"):
        run(c2.get_json("https://www.boe.es/x"))


def test_timeout_se_reintenta():
    n = {"i": 0}

    def handler(req):
        n["i"] += 1
        if n["i"] < 3:
            raise httpx.ReadTimeout("lento", request=req)
        return httpx.Response(200, json={"ok": True})

    async def sleep(_):
        pass

    c = BoeClient(transport=httpx.MockTransport(handler), min_interval=0, sleep=sleep)
    assert run(c.get_json("https://www.boe.es/x")) == {"ok": True} and n["i"] == 3


def test_throttle_entre_peticiones():
    esperas = []

    async def sleep(t):
        esperas.append(t)

    c = BoeClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})), min_interval=5, sleep=sleep)

    async def dos():
        await c.get_json("https://www.boe.es/a")
        await c.get_json("https://www.boe.es/b")

    run(dos())
    assert len(esperas) >= 1 and 0 < esperas[-1] <= 5  # la segunda petición espera


def test_cache_en_disco(tmp_path):
    llamadas = {"n": 0}

    def handler(req):
        llamadas["n"] += 1
        return httpx.Response(200, json={"n": llamadas["n"]})

    c = BoeClient(transport=httpx.MockTransport(handler), cache_dir=tmp_path, min_interval=0)
    assert run(c.get_json("https://www.boe.es/a", {"q": "ñ"}, ttl=60)) == {"n": 1}
    assert run(c.get_json("https://www.boe.es/a", {"q": "ñ"}, ttl=60)) == {"n": 1}  # de caché
    assert run(c.get_json("https://www.boe.es/a", {"q": "otra"}, ttl=60)) == {"n": 2}
    assert run(c.get_json("https://www.boe.es/a", {"q": "ñ"}, ttl=0)) == {"n": 3}  # ttl=0: sin caché
    assert llamadas["n"] == 3
    sin = BoeClient(transport=httpx.MockTransport(handler), cache_dir=None, min_interval=0)
    run(sin.get_json("https://www.boe.es/a", ttl=60)); run(sin.get_json("https://www.boe.es/a", ttl=60))
    assert llamadas["n"] == 5  # sin directorio de caché no cachea


def test_errores_no_se_cachean(tmp_path):
    c = BoeClient(transport=httpx.MockTransport(lambda r: httpx.Response(404, text="x")), cache_dir=tmp_path, min_interval=0)
    with pytest.raises(BoeError):
        run(c.get_json("https://www.boe.es/a", ttl=60))
    assert list(tmp_path.glob("*.json")) == []


# --------------------------------------------------------------- servidor
def test_herramientas_registradas():
    tools = run(S.mcp.list_tools())
    assert {t.name for t in tools} == {"sumario_boe", "buscar_legislacion", "leer_norma", "leer_documento", "novedades"}
    assert all(t.description for t in tools)


def test_fixtures_no_contienen_secretos():
    for f in FIX.iterdir():
        t = f.read_text(encoding="utf-8").lower()
        assert "api_key" not in t and "password" not in t and "bearer " not in t


def test_herramientas_mcp_de_extremo_a_extremo(client, monkeypatch):
    """Llama a las 5 herramientas por el propio MCP (firma + serialización), con red simulada."""
    monkeypatch.setattr(S, "_client", client)
    monkeypatch.setattr(P, "hoy_madrid", lambda: HOY)

    def llamar(nombre, args):
        res = run(S.mcp.call_tool(nombre, args))
        contenido = res[1] if isinstance(res, tuple) else res  # contenido estructurado si lo hay
        return contenido

    assert llamar("buscar_legislacion", {"texto": "impuesto valor añadido", "limite": 3, "donde": "auto"})["total_devueltos"] == 3
    assert llamar("sumario_boe", {"fecha": "2026-09-30"})["fecha"] == "2026-09-30"
    assert llamar("leer_norma", {"id": "BOE-A-1992-28740", "bloque": "a1"})["bloques_incluidos"] == ["a1"]
    assert llamar("leer_documento", {"id": "BOE-A-2026-20384"})["rango"] == "Real Decreto"
    assert llamar("novedades", {"materias": ["vivienda"], "dias": 2})["total_coincidencias"] >= 1
