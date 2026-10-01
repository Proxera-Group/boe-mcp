"""Servidor MCP de BOE. Herramientas: sumario_boe, buscar_legislacion, leer_norma,
leer_documento y novedades.

Las funciones de `_core` reciben el cliente para poder probarse sin red; las
herramientas MCP son envoltorios finos sobre ellas.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import urllib.parse
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import parsing as P
from .client import API, BASE, BoeClient, BoeError, BoeNotFound
from .perfiles import coincidencias, resolver_perfiles

DIA = 86400
ID_RE = re.compile(r"^BOE-[A-Z]-\d{4}-\d{1,7}$")
MAX_BLOQUES = 15
NORMA_CORTA = 25  # hasta este nº de bloques se devuelve la norma entera

_vocab_materias: dict[str, str] | None = None


def _check_id(id_: str) -> str:
    id_ = (id_ or "").strip().upper()
    if not ID_RE.match(id_):
        raise BoeError(f"Identificador no válido: {id_!r}. Formato esperado: BOE-A-2026-20385.")
    return id_


def _truncar(texto: str, inicio: int, maximo: int) -> tuple[str, bool]:
    maximo = max(1000, min(maximo, 200_000))
    inicio = max(0, inicio)
    trozo = texto[inicio : inicio + maximo]
    return trozo, inicio + maximo < len(texto)


# ----------------------------------------------------------------- sumario


async def sumario_core(
    c: BoeClient, fecha: str = "hoy", incluir_anuncios: bool = False, filtro: str | None = None,
    hoy: dt.date | None = None,
) -> dict[str, Any]:
    dia = P.parse_fecha(fecha, hoy)
    ref = hoy or P.hoy_madrid()
    if dia > ref:
        raise BoeError(f"{dia.isoformat()} es una fecha futura: ese BOE aún no existe.")
    try:
        data = await c.get_json(f"{API}/boe/sumario/{dia:%Y%m%d}", ttl=600 if dia == ref else DIA)
    except BoeNotFound:
        motivo = "los domingos no se publica el BOE" if dia.weekday() == 6 else (
            "aún no se ha publicado (el sumario suele estar a primera hora)" if dia == ref else "festivo o sin publicación"
        )
        raise BoeError(f"No hay sumario del BOE para el {dia.isoformat()}: {motivo}.") from None

    s = P.normalize_sumario(data)
    f = P.fold(filtro).strip() if filtro else None
    omitidas = 0
    secciones = []
    for sec in s["secciones"]:
        if P.es_anuncio(sec) and not incluir_anuncios:
            omitidas += sum(len(d["disposiciones"]) for d in sec["departamentos"])
            continue
        deps = []
        for d in sec["departamentos"]:
            items = d["disposiciones"]
            if f:
                items = [i for i in items if f in P.fold(f"{i['titulo']} {i['epigrafe'] or ''} {d['nombre']}")]
            if items:
                deps.append({"nombre": d["nombre"], "disposiciones": items})
        if deps:
            secciones.append({**sec, "departamentos": deps})
    total = sum(len(d["disposiciones"]) for sec in secciones for d in sec["departamentos"])
    out: dict[str, Any] = {
        "fecha": s["fecha"], "boletines": s["boletines"], "total_disposiciones": total, "secciones": secciones,
    }
    if omitidas:
        out["anuncios_omitidos"] = omitidas
        out["aviso"] = "Sección V (anuncios) omitida; usa incluir_anuncios=true para verla."
    return out


# ------------------------------------------------------------- legislación


async def _materias(c: BoeClient) -> dict[str, str]:
    global _vocab_materias
    if _vocab_materias is None:
        data = await c.get_json(f"{API}/datos-auxiliares/materias", ttl=7 * DIA)
        _vocab_materias = data.get("data") or {}
    return _vocab_materias


async def buscar_core(
    c: BoeClient, texto: str = "", materia: str | None = None, desde: str | None = None, hasta: str | None = None,
    limite: int = 10, rango: str | None = None, solo_vigentes: bool = False, ordenar_por_fecha: bool = False,
    donde: str = "auto",
) -> dict[str, Any]:
    limite = max(1, min(int(limite), 50))
    if donde not in ("auto", "titulo", "texto", "ambos"):
        raise BoeError("`donde` debe ser 'auto', 'titulo', 'texto' o 'ambos'.")
    codes = P.resolver_materia(materia, await _materias(c)) if materia else None
    rango_code = None
    if rango:
        rango_code = P.RANGOS.get(P.fold(rango).strip())
        if rango_code is None:
            raise BoeError(f"Rango desconocido: {rango!r}. Ejemplos: Ley, Ley Orgánica, Real Decreto, Real Decreto-ley, Orden, Resolución.")
    d = P.parse_fecha(desde) if desde else None
    h = P.parse_fecha(hasta) if hasta else None
    async def consultar(campo: str) -> list[dict]:
        query = P.build_query(texto, codes, rango_code, d, h, solo_vigentes, ordenar_por_fecha, campo)
        data = await c.get_json(
            f"{API}/legislacion-consolidada",
            {"query": json.dumps(query, ensure_ascii=False), "limit": limite},
            ttl=3600,
        )
        return P.parse_busqueda(data)

    usado = "titulo" if donde == "auto" else donde
    resultados = await consultar(usado)
    if not resultados and donde == "auto" and texto.strip():
        usado = "texto"  # nada en los títulos: probar en el texto completo de las normas
        resultados = await consultar(usado)
    out: dict[str, Any] = {"total_devueltos": len(resultados), "buscado_en": usado if texto.strip() else None, "resultados": resultados}
    if codes:
        out["materia_codigos"] = codes
    if len(resultados) == limite:
        out["aviso"] = "Puede haber más resultados; aumenta `limite` (máx. 50) o afina la búsqueda."
    out["nota"] = "Solo legislación consolidada. Para disposiciones recién publicadas usa sumario_boe o novedades."
    return out


async def norma_core(
    c: BoeClient, id_: str, bloque: str | None = None, max_caracteres: int = 30000, hoy: dt.date | None = None,
) -> dict[str, Any]:
    id_ = _check_id(id_)
    base = f"{API}/legislacion-consolidada/id/{id_}"
    try:
        meta = P.parse_metadatos(await c.get_json(f"{base}/metadatos", ttl=3600))
        indice = P.parse_indice(await c.get_json(f"{base}/texto/indice", ttl=3600))
    except BoeNotFound:
        raise BoeError(f"{id_} no está en la legislación consolidada. Si es una disposición del diario, usa leer_documento.") from None
    out: dict[str, Any] = {**meta}

    pedidos = [b.strip() for b in bloque.split(",") if b.strip()] if bloque else []
    if len(pedidos) > MAX_BLOQUES:
        raise BoeError(f"Pide como máximo {MAX_BLOQUES} bloques por llamada.")

    if pedidos:
        validos = {b["id"] for b in indice}
        desconocidos = [b for b in pedidos if b not in validos]
        if desconocidos:
            raise BoeError(f"Bloque(s) inexistentes en {id_}: {', '.join(desconocidos)}. Consulta el índice con leer_norma sin `bloque`.")
        bloques = []
        for b in pedidos:
            xml = await c.get_text(f"{base}/texto/bloque/{urllib.parse.quote(b, safe='')}", accept="application/xml", ttl=6 * 3600)
            bloques.extend(P.parse_bloques(xml, hoy))
    elif len(indice) <= NORMA_CORTA:
        xml = await c.get_text(f"{base}/texto", accept="application/xml", ttl=6 * 3600)
        bloques = P.parse_bloques(xml, hoy)
    else:
        analisis = P.parse_analisis(await c.get_json(f"{base}/analisis", ttl=3600))
        out.update(analisis)
        out["indice"] = [f"{b['id']} · {b['titulo'] or '(sin título)'} · actualizado {b['actualizado']}" for b in indice]
        out["aviso"] = (
            f"Norma extensa ({len(indice)} bloques): no se devuelve el texto entero. "
            "Vuelve a llamar con bloque='a1,a2,…' usando los ids del índice (máx. 15 por llamada)."
        )
        return out

    if not pedidos:
        out.update(P.parse_analisis(await c.get_json(f"{base}/analisis", ttl=3600)))
    partes = []
    pendientes = False
    for b in bloques:
        if b["aun_no_en_vigor"]:
            estado = f"AÚN NO EN VIGOR: entra en vigor el {b['vigente_desde']}"
            pendientes = True
        else:
            estado = f"vigente desde {b['vigente_desde']}; {b['versiones']} versión/es"
        parte = f"## {b['titulo'] or b['id']}  ({estado})\n{b['texto']}"
        if b["proxima"]:
            pendientes = True
            parte += f"\n\n### Próxima versión de este bloque, en vigor desde {b['proxima']['desde']}\n{b['proxima']['texto']}"
        partes.append(parte)
    if pendientes:
        out["aviso_vigencia"] = "Hay texto publicado que aún no está en vigor (marcado en el texto); la versión «vigente» es la de hoy."
    texto, mas = _truncar("\n\n".join(partes), 0, max_caracteres)
    out["bloques_incluidos"] = [b["id"] for b in bloques]
    out["texto"] = texto
    if mas:
        out["truncado"] = True
        out["aviso"] = "Texto recortado por tamaño; pide menos bloques o sube max_caracteres."
    return out


async def documento_core(c: BoeClient, id_: str, inicio: int = 0, max_caracteres: int = 30000) -> dict[str, Any]:
    id_ = _check_id(id_)
    try:
        xml = await c.get_text(f"{BASE}/diario_boe/xml.php", {"id": id_}, accept="application/xml", ttl=DIA)
    except BoeError as exc:
        raise BoeError(f"No se pudo obtener {id_} del diario: {exc}") from None
    doc = P.parse_documento(xml)
    texto, mas = _truncar(doc["texto"], inicio, max_caracteres)
    doc["longitud_total"] = len(doc["texto"])
    doc["texto"] = texto
    doc["url_html"] = f"{BASE}/diario_boe/txt.php?id={id_}"
    if mas:
        doc["truncado"] = True
        doc["aviso"] = f"Texto recortado; pide el resto con inicio={max(0, inicio) + len(texto)}."
    return doc


# --------------------------------------------------------------- novedades


async def novedades_core(
    c: BoeClient, materias: list[str], dias: int = 7, limite: int = 60,
    incluir_anuncios: bool = False, incluir_personal: bool = False, hoy: dt.date | None = None,
) -> dict[str, Any]:
    if not materias:
        raise BoeError("Indica al menos una materia: fiscal, laboral, mercantil, vivienda, datos (o palabras libres).")
    dias = max(1, min(int(dias), 31))
    limite = max(1, min(int(limite), 200))
    activos, _ = resolver_perfiles(materias)
    ref = hoy or P.hoy_madrid()

    resultados, sin_boletin = [], []
    for n in range(dias):
        dia = ref - dt.timedelta(days=n)
        if dia.weekday() == 6:  # domingo: el BOE no se publica
            continue
        try:
            data = await c.get_json(f"{API}/boe/sumario/{dia:%Y%m%d}", ttl=600 if dia == ref else DIA)
        except BoeNotFound:
            sin_boletin.append(dia.isoformat())
            continue
        s = P.normalize_sumario(data)
        for sec in s["secciones"]:
            if (P.es_anuncio(sec) and not incluir_anuncios) or (str(sec["codigo"]).startswith("2") and not incluir_personal):
                continue
            for d in sec["departamentos"]:
                for i in d["disposiciones"]:
                    perfiles = coincidencias(f"{i['titulo']} {i['epigrafe'] or ''}", activos)
                    if perfiles:
                        resultados.append(
                            {"fecha": s["fecha"], "id": i["id"], "titulo": i["titulo"], "seccion": sec["nombre"],
                             "departamento": d["nombre"], "coincide_con": perfiles,
                             "url_html": i["url_html"], "url_pdf": i["url_pdf"]}
                        )
    total = len(resultados)
    out: dict[str, Any] = {
        "periodo": f"{(ref - dt.timedelta(days=dias - 1)).isoformat()} a {ref.isoformat()}",
        "criterios": list(activos),
        "total_coincidencias": total,
        "resultados": resultados[:limite],
        "dias_sin_boletin": sin_boletin,
        "nota": "Filtro por palabras clave sobre el título: puede haber falsos positivos y omisiones. "
                "Sección II (personal) y V (anuncios) excluidas salvo que se pidan.",
    }
    if total > limite:
        out["aviso"] = f"Hay {total} coincidencias; se muestran {limite}. Sube `limite` o afina las materias."
    return out


# ---------------------------------------------------------------- servidor

mcp = FastMCP(
    "boe",
    instructions=(
        "Consulta el BOE (Boletín Oficial del Estado) vía su API oficial de datos abiertos. "
        "Úsalo para sumarios diarios, legislación consolidada y disposiciones. No es asesoramiento "
        "jurídico: cita siempre el identificador BOE y recuerda que manda el texto oficial en boe.es."
    ),
)

_client: BoeClient | None = None


def _c() -> BoeClient:
    global _client
    if _client is None:
        _client = BoeClient()
    return _client


@mcp.tool()
async def sumario_boe(fecha: str = "hoy", incluir_anuncios: bool = False, filtro: str | None = None) -> dict[str, Any]:
    """Sumario del BOE de un día, agrupado por sección y departamento (id, título, URLs html/pdf).

    fecha: AAAA-MM-DD, 'hoy' o 'ayer'. Los domingos no hay BOE.
    incluir_anuncios: la sección V (contratación, anuncios) se omite por defecto por volumen.
    filtro: texto opcional (sin distinguir tildes) para quedarse solo con lo que lo contenga.
    """
    return await sumario_core(_c(), fecha, incluir_anuncios, filtro)


@mcp.tool()
async def buscar_legislacion(
    texto: str = "", materia: str | None = None, desde: str | None = None, hasta: str | None = None,
    limite: int = 10, rango: str | None = None, solo_vigentes: bool = False, ordenar_por_fecha: bool = False,
    donde: str = "auto",
) -> dict[str, Any]:
    """Busca en la legislación consolidada (título y texto). Devuelve id, título, rango, fechas, vigencia y URL.

    texto: palabras que deben aparecer (todas). materia: tema del vocabulario del BOE, p. ej.
    'Arrendamientos urbanos' o 'Impuesto sobre Sociedades'. desde/hasta: fecha de publicación
    (AAAA-MM-DD). rango: Ley, Ley Orgánica, Real Decreto, Real Decreto-ley, Orden, Resolución…
    limite: 1-50. Por defecto ordena por relevancia. donde: 'auto' (título y, si no hay resultados,
    texto completo), 'titulo', 'texto' o 'ambos'.
    """
    return await buscar_core(_c(), texto, materia, desde, hasta, limite, rango, solo_vigentes, ordenar_por_fecha, donde)


@mcp.tool()
async def leer_norma(id: str, bloque: str | None = None, max_caracteres: int = 30000) -> dict[str, Any]:
    """Lee una norma consolidada por su id (BOE-A-AAAA-NNNN): metadatos, materias, referencias e índice.

    Si la norma es corta devuelve el texto vigente completo; si es larga devuelve solo el índice
    y hay que pedir bloques concretos con `bloque` (ids separados por comas, p. ej. 'a1,a2';
    máx. 15 por llamada). Del texto se da la versión vigente de cada bloque.
    """
    return await norma_core(_c(), id, bloque, max_caracteres)


@mcp.tool()
async def leer_documento(id: str, inicio: int = 0, max_caracteres: int = 30000) -> dict[str, Any]:
    """Texto de una disposición publicada en el diario por su id (BOE-A-AAAA-NNNN), con materias y referencias.

    Útil para lo recién publicado (que quizá aún no esté consolidado). Para textos largos use
    `inicio` (desplazamiento en caracteres) para paginar.
    """
    return await documento_core(_c(), id, inicio, max_caracteres)


@mcp.tool()
async def novedades(
    materias: list[str], dias: int = 7, limite: int = 60, incluir_anuncios: bool = False, incluir_personal: bool = False,
) -> dict[str, Any]:
    """Lo publicado en el BOE en los últimos N días que toca unos temas, filtrando los sumarios por palabras clave.

    materias: perfiles 'fiscal' (IVA, IRPF, tributos), 'laboral' (Seguridad Social, convenios),
    'mercantil' (sociedades, concursal), 'vivienda' (alquiler, arrendamientos) y 'datos'
    (protección de datos), o palabras libres. dias: 1-31. Excluye por defecto la sección II
    (nombramientos, oposiciones) y la V (anuncios).
    """
    return await novedades_core(_c(), materias, dias, limite, incluir_anuncios, incluir_personal)


def main() -> None:
    import logging

    logging.getLogger("httpx").setLevel(logging.WARNING)  # no volcar cada petición a stderr
    mcp.run()


if __name__ == "__main__":
    main()
