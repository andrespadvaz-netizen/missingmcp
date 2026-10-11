# Health OS: gap de transporte Apple Health

## Decisión de diseño

ChatGPT Health es la interfaz primaria de consulta e interpretación. No se
considera transporte canónico hasta que pueda entregar registros crudos,
estructurados, con muestra, timestamp, unidad y procedencia.

## Restricción

Apple Health no ofrece una API servidor-a-servidor general y la conexión Health
de ChatGPT no acredita por sí misma una exportación automática hacia Metis o
Notion. Copiar valores manualmente no es una solución aceptable: convierte
narrativa de modelo en evidencia.

## Adaptador mínimo aceptable

Un transporte productivo deberá usar autorización mínima por categoría, enviar
sólo registros crudos a `/health-os/ingest`, conservar `provenance_id`, offset,
unidad y semántica, cifrar persistencia y demostrar cinco días consecutivos de
cobertura sin intervención manual.

No se selecciona ni instala una herramienta nueva con este documento. Esa
selección requiere evidencia de interfaz y autorización específica del operador.
