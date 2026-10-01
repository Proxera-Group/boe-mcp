# boe-mcp

Servidor [MCP](https://modelcontextprotocol.io) para que **Claude consulte el BOE** (Boletín Oficial del Estado) con la API oficial de datos abiertos de boe.es: sumarios diarios, legislación consolidada y disposiciones del diario.

Está pensado para el trabajo diario de **gestorías, asesorías fiscales y laborales y despachos de abogados** en España: saber qué se ha publicado hoy sobre IVA o Seguridad Social, localizar la norma vigente, leer un artículo concreto con su redacción actual o ver qué modifica una norma recién publicada.

- Sin claves ni cuentas: la API del BOE es pública.
- Una sola dependencia propia (`httpx`) además del SDK oficial de MCP.
- Respetuoso con boe.es: User-Agent identificable, timeouts, reintentos suaves, pausa entre peticiones y caché opcional.
- No envía tus consultas a ningún sitio que no sea boe.es.

## Herramientas

| Herramienta | Qué hace |
|---|---|
| `sumario_boe(fecha, incluir_anuncios?, filtro?)` | Sumario del BOE de un día, agrupado por sección y departamento, con id, título y URLs (html/pdf). |
| `buscar_legislacion(texto, materia?, desde?, hasta?, limite, rango?, solo_vigentes?, donde?)` | Búsqueda en la legislación consolidada: id, título, rango, fechas, vigencia y URL. |
| `leer_norma(id, bloque?)` | Metadatos, materias, referencias y texto **vigente** de una norma consolidada. Si es larga devuelve el índice y se piden bloques (`a1,a2`…). |
| `leer_documento(id)` | Texto de una disposición del diario por su `BOE-A-AAAA-NNNN` (útil para lo recién publicado), con paginación. |
| `novedades(materias, dias)` | Lo publicado en los últimos N días sobre unos temas, filtrando los sumarios por palabras clave. |

Perfiles de `novedades`: `fiscal` (IVA, IRPF, tributos…), `laboral` (Seguridad Social, convenios…), `mercantil` (sociedades, concursal, registros…), `vivienda` (alquiler, arrendamientos, hipotecas…) y `datos` (protección de datos). También acepta palabras libres.

## Instalación

Necesitas Python 3.11 o superior. La forma más cómoda es [`uv`](https://docs.astral.sh/uv/), que descarga y ejecuta el servidor sin instalar nada a mano.

> El paquete se publica en PyPI como **`boe-mcp-es`** (el nombre `boe-mcp` estaba ocupado). El ejecutable se llama `boe-mcp`.

### Claude Code

```bash
claude mcp add boe -- uvx --from boe-mcp-es boe-mcp
```

### Claude Desktop

Edita `claude_desktop_config.json` (Ajustes → Desarrollador → Editar configuración) y añade:

```json
{
  "mcpServers": {
    "boe": {
      "command": "uvx",
      "args": ["--from", "boe-mcp-es", "boe-mcp"]
    }
  }
}
```

Reinicia Claude Desktop. Si prefieres `pip`:

```bash
pip install boe-mcp-es
```

y usa `"command": "boe-mcp"` (sin `args`) en la configuración.

### Caché en disco (opcional)

```bash
export BOE_MCP_CACHE_DIR=~/.cache/boe-mcp
```

En Claude Desktop se añade con `"env": { "BOE_MCP_CACHE_DIR": "/ruta/a/la/cache" }`. Guarda sumarios pasados, normas y búsquedas durante un tiempo corto (el sumario de hoy, 10 minutos). Sin la variable no se cachea nada.

## Ejemplos de preguntas

1. **«¿Qué ha salido hoy en el BOE sobre IVA?»** → `sumario_boe` / `novedades(["fiscal"], 1)`.
2. **«Resume los cambios del Real Decreto-ley 27/2026 en arrendamientos.»** → `buscar_legislacion` + `leer_norma`: ve qué artículos de la LAU modifica y su redacción.
3. **«¿Cuál es la redacción actual del artículo 10 de la Ley de Arrendamientos Urbanos y entra algún cambio en vigor próximamente?»** → `leer_norma(id, bloque="a10")` distingue la versión vigente hoy de la que entra en vigor más adelante.
4. **«Dame las novedades laborales y de Seguridad Social de las últimas dos semanas.»** → `novedades(["laboral"], 14)`.
5. **«¿Qué normas regulan la protección de datos y siguen vigentes?»** → `buscar_legislacion(texto="protección de datos", solo_vigentes=true)`.

## Cómo conviene usarlo

- Pide siempre que cite el **identificador BOE** (`BOE-A-2026-20385`) y el enlace: así puedes comprobar la fuente en segundos.
- Para normas largas (la LIVA tiene más de 300 bloques) Claude lee el índice y luego solo los artículos que necesita.
- `novedades` filtra por palabras clave en el título: sirve para vigilar, no para garantizar que no se escapa nada.

## Limitaciones

- **No es asesoramiento jurídico ni fiscal.** Es una herramienta de consulta. **La fuente oficial manda**: contrasta siempre con el texto en [boe.es](https://www.boe.es) antes de actuar o de asesorar a un cliente.
- Claude puede resumir o interpretar mal un texto legal. Revisa lo importante contra el original.
- Cubre lo que ofrece la API del BOE: el BOE y la legislación consolidada que el BOE mantiene (incluye parte de la normativa autonómica publicada en el BOE). No incluye DOUE, boletines autonómicos propios, jurisprudencia ni doctrina administrativa (consultas de la DGT, etc.).
- La legislación consolidada puede ir por detrás del diario: una norma publicada hoy puede tardar en consolidarse. Para lo recién publicado, usa `leer_documento`.
- El campo «vigencia» es el que declara el BOE (vigente / vigencia agotada / derogada). Las normas **derogadas solo en parte** figuran como vigentes: lee siempre la norma y sus referencias.
- Los domingos no se publica BOE; el sumario del día suele estar disponible a primera hora.
- Un texto consolidado puede contener modificaciones publicadas que **aún no han entrado en vigor**; el servidor devuelve la versión vigente a fecha de hoy y avisa de la siguiente, pero comprueba las fechas.
- Los anuncios (sección V) y el personal (sección II) se omiten por defecto en sumarios y novedades por volumen.
- Se hace una petición cada ≥0,4 s como máximo y pedir muchos días en `novedades` tarda unos segundos. Sé amable con el servicio.

## Desarrollo

```bash
git clone <este repositorio> && cd boe-mcp
uv venv && uv pip install -e ".[dev]"
.venv/bin/pytest          # no usa red: respuestas reales grabadas en tests/fixtures/
```

Los tests usan respuestas reales de boe.es guardadas en `tests/fixtures/` y fallan si intentan abrir un socket.

## Fuente y licencia

Los datos proceden de la [API de datos abiertos del BOE](https://www.boe.es/datosabiertos/) y están sujetos a su [aviso legal](https://www.boe.es/informacion/aviso_legal/). Este proyecto no está afiliado a la Agencia Estatal Boletín Oficial del Estado.

Código bajo licencia [MIT](LICENSE) © Proxera AI Solutions Group S.L.

---

Hecho por Proxera (proxera.es) — IA para gestorías y despachos
