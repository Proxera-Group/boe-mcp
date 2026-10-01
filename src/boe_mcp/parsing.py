"""Normalización de las respuestas de boe.es a estructuras sencillas.

La API devuelve JSON generado desde XML, así que un mismo campo puede venir como
objeto, como lista o como cadena vacía según haya 0, 1 o N elementos. Todo se
pasa por `as_list` y `unwrap` para que el resto del código no lo tenga que saber.
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
import xml.etree.ElementTree as ET
from typing import Any

from .client import BoeError

# ---------------------------------------------------------------- utilidades


def as_list(x: Any) -> list:
    if x in (None, "", {}):
        return []
    return x if isinstance(x, list) else [x]


def unwrap(node: Any) -> dict:
    """Aplana el envoltorio `texto` que a veces trae un nodo (departamento, sección)."""
    if not isinstance(node, dict):
        return {}
    inner = node.get("texto")
    if isinstance(inner, dict):
        merged = {k: v for k, v in node.items() if k != "texto"}
        merged.update(inner)
        return merged
    return node


def fold(s: str) -> str:
    """Minúsculas y sin tildes, para comparar texto español."""
    s = unicodedata.normalize("NFD", s)
    return "".join(c for c in s if unicodedata.category(c) != "Mn").lower()


def fmt_fecha(s: str | None) -> str | None:
    """AAAAMMDD (o AAAAMMDDTHHMMSSZ) -> AAAA-MM-DD."""
    if not s or len(s) < 8 or not s[:8].isdigit():
        return None
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def hoy_madrid() -> dt.date:
    """Fecha de hoy en España (el BOE se publica en hora peninsular)."""
    try:
        from zoneinfo import ZoneInfo

        return dt.datetime.now(ZoneInfo("Europe/Madrid")).date()
    except Exception:  # sin base de datos de zonas horarias (p. ej. Windows sin tzdata)
        return dt.date.today()


def parse_fecha(valor: str | None, hoy: dt.date | None = None) -> dt.date:
    """Acepta 'hoy', 'ayer', AAAA-MM-DD, AAAAMMDD y DD/MM/AAAA."""
    hoy = hoy or hoy_madrid()
    v = (valor or "hoy").strip().lower()
    if v in ("hoy", "today", ""):
        return hoy
    if v == "ayer":
        return hoy - dt.timedelta(days=1)
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return dt.datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    raise BoeError(f"Fecha no válida: {valor!r}. Usa AAAA-MM-DD (por ejemplo 2026-09-30), 'hoy' o 'ayer'.")


# ------------------------------------------------------------------- sumario


def _item_urls(item: dict) -> tuple[str | None, str | None]:
    pdf = item.get("url_pdf")
    if isinstance(pdf, dict):
        pdf = pdf.get("texto")
    return item.get("url_html"), pdf


def _norm_item(item: dict, epigrafe: str | None) -> dict:
    html, pdf = _item_urls(item)
    return {
        "id": item.get("identificador"),
        "titulo": (item.get("titulo") or "").strip(),
        "epigrafe": epigrafe,
        "url_html": html,
        "url_pdf": pdf,
    }


def normalize_sumario(data: dict) -> dict:
    """JSON del sumario -> {fecha, boletines, secciones:[{codigo, nombre, boletin, departamentos:[...]}]}."""
    try:
        sumario = data["data"]["sumario"]
    except (KeyError, TypeError) as exc:
        raise BoeError("Respuesta de sumario con formato inesperado.") from exc

    boletines, secciones = [], []
    for diario in as_list(sumario.get("diario")):
        sd = diario.get("sumario_diario") or {}
        pdf = sd.get("url_pdf")
        boletines.append(
            {
                "numero": diario.get("numero"),
                "id": sd.get("identificador"),
                "url_pdf": pdf.get("texto") if isinstance(pdf, dict) else pdf,
            }
        )
        for sec in as_list(diario.get("seccion")):
            sec = sec if isinstance(sec, dict) else {}
            sec_u = unwrap(sec)
            departamentos = []
            for dep in as_list(sec_u.get("departamento")):
                dep_u = unwrap(dep)
                items = [_norm_item(i, None) for i in as_list(dep_u.get("item"))]
                for ep in as_list(dep_u.get("epigrafe")):
                    nombre_ep = (ep.get("nombre") or "").strip() or None
                    items += [_norm_item(i, nombre_ep) for i in as_list(ep.get("item"))]
                if items:
                    departamentos.append({"nombre": dep.get("nombre"), "disposiciones": items})
            secciones.append(
                {
                    "codigo": sec.get("codigo"),
                    "nombre": sec.get("nombre"),
                    "boletin": diario.get("numero"),
                    "departamentos": departamentos,
                }
            )
    return {
        "fecha": fmt_fecha(sumario.get("metadatos", {}).get("fecha_publicacion")),
        "boletines": boletines,
        "secciones": secciones,
    }


def es_anuncio(seccion: dict) -> bool:
    """Sección V (anuncios: contratación pública, otros anuncios, particulares)."""
    return str(seccion.get("codigo", "")).startswith("5")


# -------------------------------------------------------- legislación (lista)


def parse_busqueda(data: dict) -> list[dict]:
    rows = []
    for x in as_list(data.get("data")):
        agotada = x.get("vigencia_agotada") == "S"
        rows.append(
            {
                "id": x.get("identificador"),
                "titulo": (x.get("titulo") or "").strip(),
                "rango": (x.get("rango") or {}).get("texto"),
                "departamento": (x.get("departamento") or {}).get("texto"),
                "fecha_disposicion": fmt_fecha(x.get("fecha_disposicion")),
                "fecha_publicacion": fmt_fecha(x.get("fecha_publicacion")),
                "en_vigor_desde": fmt_fecha(x.get("fecha_vigencia")),
                "vigencia": "vigencia agotada" if agotada else "vigente",
                "ultima_actualizacion": fmt_fecha(x.get("fecha_actualizacion")),
                "url": x.get("url_html_consolidada"),
                "eli": x.get("url_eli"),
            }
        )
    return rows


RANGOS = {
    "acuerdo": 1020, "acuerdo internacional": 1180, "circular": 1390, "constitucion": 1070,
    "decreto": 1510, "decreto foral legislativo": 1480, "decreto legislativo": 1470,
    "decreto-ley": 1500, "decreto ley": 1500, "decreto-ley foral": 1325, "instruccion": 1410,
    "ley": 1300, "ley foral": 1450, "ley organica": 1290, "orden": 1350, "real decreto": 1340,
    "real decreto legislativo": 1310, "real decreto-ley": 1320, "real decreto ley": 1320,
    "reglamento": 1220, "resolucion": 1370,
}  # fuente: /datosabiertos/api/datos-auxiliares/rangos


def build_query(
    texto: str,
    materia_codes: list[int] | None = None,
    rango: int | None = None,
    desde: dt.date | None = None,
    hasta: dt.date | None = None,
    solo_vigentes: bool = False,
    ordenar_fecha: bool = False,
    donde: str = "titulo",
) -> dict:
    """Construye el JSON del parámetro `query` de la API de legislación consolidada.

    Cosas que se han comprobado contra la API real (oct-2026):
    - los caracteres especiales en query_string provocan un 500, por eso el texto
      se reduce a palabras; 'and/or/not' se descartan.
    - `range` solo se aplica si van `gte` y `lte` a la vez.
    - una query_string vacía junto a `range` da 500: en ese caso se omite.
    """
    words = [w for w in re.findall(r"[\w/]+", texto or "", re.UNICODE) if w.lower() not in ("and", "or", "not")]
    parts: list[str] = []
    if words:
        grupo = " and ".join(words)
        if donde == "texto":
            parts.append(f"texto:({grupo})")
        elif donde == "ambos":
            parts.append(f"(titulo:({grupo}) or texto:({grupo}))")
        else:
            parts.append(f"titulo:({grupo})")
    if materia_codes:
        parts.append("materia@codigo:(" + " or ".join(str(c) for c in materia_codes) + ")")
    if rango:
        parts.append(f"rango@codigo:{rango}")
    if solo_vigentes:
        parts.append("vigencia_agotada:N")

    query: dict[str, Any] = {}
    if parts:
        query["query_string"] = {"query": " and ".join(parts)}
    if desde or hasta:
        desde = desde or dt.date(1000, 1, 1)
        hasta = hasta or dt.date(2999, 12, 31)
        query["range"] = {"fecha_publicacion": {"gte": desde.strftime("%Y%m%d"), "lte": hasta.strftime("%Y%m%d")}}
    if not query:
        raise BoeError("Indica al menos un texto, una materia, un rango o un intervalo de fechas.")
    body: dict[str, Any] = {"query": query}
    if ordenar_fecha:
        body["sort"] = [{"fecha_publicacion": "desc"}]
    return body


def resolver_materia(nombre: str, vocabulario: dict[str, str]) -> list[int]:
    """Traduce un nombre de materia (p. ej. 'Arrendamientos urbanos') a códigos del BOE.

    Coincidencia exacta (sin tildes ni mayúsculas); si no la hay, solo se acepta una
    coincidencia parcial única. Con varias se pide concretar, porque el vocabulario
    mezcla temas y organismos y juntarlas devolvería resultados irrelevantes.
    """
    n = fold(nombre).strip()
    if n.isdigit() and n in vocabulario:
        return [int(n)]
    exactas = [int(k) for k, v in vocabulario.items() if fold(v) == n]
    if exactas:
        return exactas
    parciales = [(int(k), v) for k, v in vocabulario.items() if n and n in fold(v)]
    if len(parciales) == 1:
        return [parciales[0][0]]
    if not parciales:
        raise BoeError(
            f"No encuentro la materia {nombre!r} en el vocabulario del BOE (tiene unas 6.800, no todos los temas). "
            "Prueba con un término más general o busca por palabras en `texto`."
        )
    ej = "; ".join(v for _, v in sorted(parciales, key=lambda p: len(p[1]))[:8])
    raise BoeError(
        f"No hay una materia exactamente llamada {nombre!r} y hay {len(parciales)} parecidas (p. ej.: {ej}). "
        "Repite con el nombre exacto de una de ellas, o busca por palabras en `texto`."
    )


# -------------------------------------------------- consolidada: norma/bloques


def parse_indice(data: dict) -> list[dict]:
    out = []
    for grupo in as_list(data.get("data")):
        for b in as_list(grupo.get("bloque")):
            out.append(
                {
                    "id": b.get("id"),
                    "titulo": (b.get("titulo") or "").strip(),
                    "actualizado": fmt_fecha(b.get("fecha_actualizacion")),
                }
            )
    return out


def parse_metadatos(data: dict) -> dict:
    rows = parse_busqueda(data)
    if not rows:
        raise BoeError("Norma sin metadatos en boe.es.")
    meta = rows[0]
    raw = as_list(data.get("data"))[0]
    if raw.get("estatus_derogacion") == "S":
        meta["vigencia"] = "derogada" + (f" ({fmt_fecha(raw.get('fecha_derogacion'))})" if raw.get("fecha_derogacion") else "")
    elif raw.get("estatus_anulacion") == "S":
        meta["vigencia"] = "anulada"
    return meta


def parse_analisis(data: dict) -> dict:
    """Materias y referencias anteriores/posteriores (qué modifica / quién la modifica)."""
    raw = (as_list(data.get("data")) or [{}])[0]
    materias = [m.get("materia", {}).get("texto") for m in as_list(raw.get("materias")) if isinstance(m, dict)]

    def refs(clave: str, sub: str, tope: int = 25) -> list[str]:
        out = []
        grupo = (raw.get("referencias") or {})
        for g in as_list(grupo.get(clave)):
            for r in as_list(g.get(sub)):
                rel = (r.get("relacion") or {}).get("texto", "")
                out.append(f"{rel} {r.get('texto', '')} [{r.get('id_norma')}]".strip())
        if len(out) > tope:
            out = out[:tope] + [f"… y {len(out) - tope} referencias más (ver boe.es)"]
        return out

    return {
        "materias": [m for m in materias if m],
        "modifica_o_cita": refs("anteriores", "anterior"),
        "modificada_por": refs("posteriores", "posterior"),
    }


_WS = re.compile(r"[ \t\r\f\v]+")


def _inline(el: ET.Element) -> str:
    return _WS.sub(" ", "".join(el.itertext())).strip()


def element_to_text(el: ET.Element) -> str:
    """Convierte un fragmento HTML-XML del BOE (p, table, blockquote, img) a texto plano."""
    lines: list[str] = []
    for child in el:
        tag = child.tag.split("}")[-1]
        if tag == "p":
            t = _inline(child)
            if t:
                lines.append(t)
        elif tag == "table":
            for tr in child.iter("tr"):
                cells = [_inline(c) for c in tr if c.tag in ("td", "th")]
                if any(cells):
                    lines.append("| " + " | ".join(cells) + " |")
        elif tag == "blockquote":
            inner = element_to_text(child)
            if inner:
                lines.append("\n".join("> " + ln for ln in inner.splitlines()))
        elif tag == "img":
            lines.append("[imagen omitida]")
        else:
            t = _inline(child)
            if t:
                lines.append(t)
    return "\n".join(lines)


def _parse_xml(text: str) -> ET.Element:
    try:
        return ET.fromstring(text.encode("utf-8") if isinstance(text, str) else text)
    except ET.ParseError as exc:
        raise BoeError("boe.es devolvió XML no válido.") from exc


def parse_bloques(xml_text: str, hoy: dt.date | None = None) -> list[dict]:
    """XML de /texto o /texto/bloque/{id} -> lista de bloques con su versión vigente.

    Cada bloque trae todas sus versiones históricas, incluidas las futuras (normas ya
    publicadas cuya modificación aún no ha entrado en vigor). Se devuelve la última
    versión con fecha de vigencia <= hoy; si hay una posterior se incluye aparte en
    `proxima`. Si ninguna está en vigor todavía, se devuelve la primera con
    `aun_no_en_vigor=True`.
    """
    hoy_s = (hoy or hoy_madrid()).strftime("%Y%m%d")
    root = _parse_xml(xml_text)
    out = []
    for b in root.iter("bloque"):
        versiones = b.findall("version")
        if not versiones:
            continue
        # sin fecha de vigencia se asume en vigor desde siempre
        en_vigor = [v for v in versiones if (v.get("fecha_vigencia") or "00000000") <= hoy_s]
        futuras = [v for v in versiones if (v.get("fecha_vigencia") or "00000000") > hoy_s]
        v = en_vigor[-1] if en_vigor else versiones[0]
        proxima = None
        if en_vigor and futuras:
            f = futuras[0]  # la más próxima
            proxima = {"desde": fmt_fecha(f.get("fecha_vigencia")), "norma_origen": f.get("id_norma"), "texto": element_to_text(f)}
        out.append(
            {
                "id": b.get("id"),
                "tipo": b.get("tipo"),
                "titulo": b.get("titulo"),
                "vigente_desde": fmt_fecha(v.get("fecha_vigencia")),
                "aun_no_en_vigor": not en_vigor,
                "publicada": fmt_fecha(v.get("fecha_publicacion")),
                "norma_origen": v.get("id_norma"),
                "versiones": len(versiones),
                "texto": element_to_text(v),
                "proxima": proxima,
            }
        )
    return out


# ------------------------------------------------------- documento del diario


def _txt(el: ET.Element | None, path: str) -> str | None:
    if el is None:
        return None
    n = el.find(path)
    if n is None or not n.text:
        return None
    return n.text.strip() or None


def parse_documento(xml_text: str) -> dict:
    root = _parse_xml(xml_text)
    if root.tag != "documento":
        raise BoeError("boe.es no devolvió un documento (¿identificador incorrecto?).")
    meta = root.find("metadatos")
    analisis = root.find("analisis")
    texto_el = root.find("texto")

    materias, refs = [], []
    if analisis is not None:
        materias = [(m.text or "").strip() for m in analisis.findall("./materias/materia")]
        for tipo, ruta in (("anterior", "./referencias/anteriores/anterior"), ("posterior", "./referencias/posteriores/posterior")):
            for r in analisis.findall(ruta):
                palabra = _txt(r, "palabra") or ""
                refs.append(f"{palabra} {_txt(r, 'texto') or ''} [{r.get('referencia')}]".strip() + ("" if tipo == "anterior" else " (posterior)"))

    return {
        "id": _txt(meta, "identificador"),
        "titulo": _txt(meta, "titulo"),
        "rango": _txt(meta, "rango"),
        "departamento": _txt(meta, "departamento"),
        "fecha_disposicion": fmt_fecha(_txt(meta, "fecha_disposicion")),
        "fecha_publicacion": fmt_fecha(_txt(meta, "fecha_publicacion")),
        "boletin_numero": _txt(meta, "diario_numero"),
        "url_pdf": _txt(meta, "url_pdf"),
        "eli": _txt(meta, "url_eli"),
        "materias": [m for m in materias if m],
        "referencias": refs,
        "texto": element_to_text(texto_el) if texto_el is not None else "",
    }
