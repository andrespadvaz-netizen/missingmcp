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

## Adaptador implementado: exportación nativa

El servicio aislado incorpora `POST /health-os/import/apple-export`. Acepta
directamente `export.xml` generado por Apple Health, extrae sólo tipos explícitamente
soportados y produce identificadores de procedencia deterministas a partir de los
campos originales de cada muestra. Los tipos desconocidos se devuelven como
`ignored_apple_types`; nunca se adivinan ni se convierten.

Este es un transporte de evidencia cruda por lotes, no una copia de conversación
ni una sincronización continua. Requiere exportar el archivo desde el iPhone y
enviarlo al servicio privado autenticado.

## Criterio restante para automatización continua

sólo un productor iPhone autorizado por categoría podrá enviar
sólo registros crudos a `/health-os/ingest`, conservar `provenance_id`, offset,
unidad y semántica, cifrar persistencia y demostrar cinco días consecutivos de
cobertura sin intervención manual.

No se selecciona ni instala una herramienta nueva con este documento. Esa
selección requiere evidencia de interfaz y autorización específica del operador.
