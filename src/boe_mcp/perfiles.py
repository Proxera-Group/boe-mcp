"""Perfiles de palabras clave para `novedades`.

Las expresiones se aplican sobre el texto en minúsculas y sin tildes (ver
`parsing.fold`), por lo que aquí se escriben sin tildes. Son heurísticas sobre
el título de la disposición: sirven para filtrar el sumario, no para
interpretarlo; conviene revisar siempre el texto oficial.
"""

from __future__ import annotations

import re

from .parsing import fold

PERFILES: dict[str, list[str]] = {
    "fiscal": [
        r"\biva\b", r"valor anadido", r"\birpf\b", r"renta de las personas fisicas",
        r"impuesto sobre", r"impuestos? especial", r"\btributari", r"\btributos?\b",
        r"agencia estatal de administracion tributaria", r"\baeat\b", r"\bretencion",
        r"\bmodelo \d{3}\b", r"\bfiscal", r"\baduaner", r"\bsii\b", r"factura electronica",
        r"\bverifactu\b", r"\bcenso\b", r"\bcatastr",
    ],
    "laboral": [
        r"seguridad social", r"convenio colectivo", r"\bcotizacion", r"\bsalari", r"\bempleo\b",
        r"\btrabajadores?\b", r"\blaboral", r"\bpension", r"\bdesempleo\b", r"\bjubilacion\b",
        r"estatuto de los trabajadores", r"prevencion de riesgos laborales", r"\berte\b",
        r"\bautonomos\b", r"trabajo autonomo", r"inspeccion de trabajo", r"\bsepe\b", r"\bsmi\b",
        r"salario minimo", r"ingreso minimo vital",
    ],
    "mercantil": [
        r"sociedades? de capital", r"\bregistro mercantil", r"\bmercantil", r"concurso de acreedores", r"\bconcursal",
        r"\binsolvencia", r"cuentas anuales", r"\bauditor", r"\bsociedad(es)? (anonima|limitada|cooperativa)",
        r"\bcooperativas?\b", r"\bemprendedor", r"\bstartups?\b", r"\bfundacion(es)?\b",
        r"\bregistro de la propiedad", r"\bnotari", r"blanqueo de capitales", r"\bbeneficiario final",
        r"impuesto sobre sociedades",
    ],
    "vivienda": [
        r"\barrendamiento", r"\balquiler", r"\bvivienda", r"\binmueble", r"propiedad horizontal",
        r"\bhipotec", r"\bdesahucio", r"\bfianza", r"\buso turistico", r"\bcertificado energetico",
        r"\bsuelo\b", r"\burbanis", r"\bcomunidades? de propietarios",
    ],
    "datos": [
        r"proteccion de datos", r"datos personales", r"\brgpd\b", r"\blopdgdd\b",
        r"agencia espanola de proteccion de datos", r"\bprivacidad", r"\bciberseguridad",
        r"esquema nacional de seguridad", r"\bservicios digitales",
        r"\bfirma electronica", r"identidad digital", r"\bcanal de denuncias", r"\binformante",
    ],
}

PERFILES_COMPILADOS = {k: [re.compile(p) for p in v] for k, v in PERFILES.items()}

ALIAS = {
    "fiscal": "fiscal", "iva": "fiscal", "irpf": "fiscal", "tributario": "fiscal", "impuestos": "fiscal",
    "laboral": "laboral", "seguridad social": "laboral", "trabajo": "laboral",
    "mercantil": "mercantil", "sociedades": "mercantil", "societario": "mercantil",
    "vivienda": "vivienda", "alquiler": "vivienda", "arrendamientos": "vivienda", "inmobiliario": "vivienda",
    "datos": "datos", "proteccion de datos": "datos", "rgpd": "datos", "privacidad": "datos",
}


def resolver_perfiles(entradas: list[str]) -> tuple[dict[str, list[re.Pattern]], list[str]]:
    """Devuelve {nombre: [patrones]} y la lista de entradas libres.

    Una entrada que sea un perfil o alias conocido activa el perfil completo; cualquier
    otra se trata como palabra o frase literal (sin tildes ni mayúsculas).
    """
    activos: dict[str, list[re.Pattern]] = {}
    libres: list[str] = []
    for e in entradas:
        clave = fold(e).strip()
        if not clave:
            continue
        perfil = ALIAS.get(clave)
        if perfil:
            activos[perfil] = PERFILES_COMPILADOS[perfil]
        else:
            activos[f"«{e.strip()}»"] = [re.compile(r"\b" + re.escape(clave))]
            libres.append(e.strip())
    return activos, libres


def coincidencias(texto: str, activos: dict[str, list[re.Pattern]]) -> list[str]:
    t = fold(texto)
    return [nombre for nombre, pats in activos.items() if any(p.search(t) for p in pats)]
