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

## Gap 7.12 — transporte diario elegido (12-oct-2026)

- **Problema:** la única entrada era la exportación manual del ZIP; sin ingesta diaria el estado cae a
  REVISAR-TÉCNICO a los dos días y las líneas base no maduran.
- **Por qué el stack no basta:** ChatGPT Health lee Apple Health pero no tiene API, webhook ni
  exportación (ayuda de OpenAI); pasar los valores por el modelo los convierte en transcripción.
  Las apps "Health Export – AI Data Share" y "Lode" solo exportan a mano. Atajos de iOS sí podría,
  pero exige construir y mantener un flujo por métrica.
- **Herramienta:** Health Auto Export – JSON+CSV (Lybron Sobers, App Store id1115567069; 4.3 con 317
  calificaciones). Automatización REST con encabezado `Authorization`, JSON, rango "desde la última
  sincronización", ejecución en segundo plano. Premium: 6.99 USD al año o 24.99 USD de por vida.
- **Costo operativo y límites declarados por el fabricante:** iOS no da acceso a Salud con el iPhone
  bloqueado; depende de la actualización en segundo plano y del modo de bajo consumo; tareas de unos
  30 segundos. Mitigación: si deja de llegar evidencia del reloj, el Brief muestra REVISAR-TÉCNICO al
  día siguiente (falla visible).
- **Entrada:** `POST /health-os/ingest/health-auto-export` (clave de ingesta). Sueño solo desagregado;
  métricas desconocidas se reportan por nombre; respuesta sin valores.
- **Criterio de éxito:** cinco días seguidos con evidencia del reloj sin intervención manual.
