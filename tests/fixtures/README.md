# Fixtures

Respuestas **reales** de la API de datos abiertos de boe.es, capturadas el 1-oct-2026
(sumarios de 29/09, 30/09 y 01/10/2026; legislación consolidada de la Ley 37/1992 del IVA
y del Real Decreto-ley 27/2026; documento BOE-A-2026-20384; respuestas de error 400/404/500).
`materias_subset.json` es un subconjunto real del vocabulario de materias.
Los tests no usan red: leen estos ficheros mediante `httpx.MockTransport`.
Contenido de fuente oficial (BOE, reutilización de información del sector público).
