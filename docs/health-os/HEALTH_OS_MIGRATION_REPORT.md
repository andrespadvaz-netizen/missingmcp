# Informe de migración Health OS

Fecha: 2026-10-11. Estado: arquitectura implementada. La conexión Health nativa
de ChatGPT móvil es la vía primaria de consulta Apple Health; no depende de
recibir 14 días para su cierre.

## Hecho

- Se añadió `missingmcp.health_os`, con normalización, procedencia,
  idempotencia, deduplicación Apple/Speediance y eras explícitas.
- La ingestión nueva rechaza Garmin. Garmin queda como `GARMIN_LEGACY` para
  conservación histórica; el servicio genérico Garmin de MissingMCP no se
  eliminó, pues no pertenece al Health OS personal.
- Se agregó `tzdata` para soporte fiable de zona horaria en Windows.
- El gateway expone ingestión privada con clave dedicada y persistencia local
  cifrada; admite Apple Health y Speediance, no Garmin.
- Se registró la enmienda de migración para v2.9 en la Cola de Enmiendas.

## Evidencia de prueba

`tests/test_health_os.py`: 12 pruebas aprobadas: idempotencia, unidades,
rechazo, solapamiento, transición, legado, zona horaria, contrato, cifrado y
endpoint privado persistente.

La suite completa llega a un único fallo de plataforma preexistente:
`tests/test_adapters.py::test_garmin_forward_materialize_writes_0600_tokens_file`
espera modo POSIX `0600`; Windows devuelve `0666`. No afecta a Health OS.

## Próximas acciones operativas (no bloquean la implementación)

1. Usar ChatGPT móvil para consultas y análisis Apple Health, guardando la
   procedencia de cada resultado.
2. La fuerza detallada de Speediance queda diferida: la fuente oficial confirma
   historial y análisis en su app, pero no publica una exportación CSV/JSON/API
   confirmada. Apple Health queda como fuente operativa de sesiones; el
   adaptador Speediance sólo se habilitará al disponer de evidencia de interfaz.
3. Ejecutar la auditoría cruzada de Claude cuando el Gateway permita lecturas
   completas. Está diferida por instrucción de Andrés, no se declara aprobada.
