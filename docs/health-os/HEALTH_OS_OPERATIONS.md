# Health OS: operación

## Productores autorizados

El núcleo admite sólo `APPLE_HEALTH` y `SPEEDIANCE`. La app móvil de ChatGPT
puede consultar métricas sincronizadas de Apple Health, pero sus respuestas son
`model_interpreted`: sirven para exploración y conversación, nunca como evidencia
canónica ni como entrada del Brief. La producción exige un productor que preserve
fecha ISO-8601 con offset, unidad, valor crudo y `provenance_id` de HealthKit.

El adaptador aislado está implementado como servicio `health-os` (sin Garmin,
login Garmin ni worker Garmin) con `Dockerfile.health-os`: `POST /health-os/ingest` y
`GET /health-os/quality`. Ambos exigen `Authorization: Bearer <HEALTH_OS_INGEST_KEY>`;
la clave debe ser distinta de `GATEWAY_SECRET` y tener 32 caracteres o más.
Los eventos se cifran localmente antes de persistirse y las respuestas no
devuelven datos personales.

`POST /health-os/brief` genera el snapshot operativo mínimo. Devuelve estado
de decisión, confianza, factores, faltantes y calidad; no devuelve registros
crudos ni ejecuta cambios de entrenamiento, agenda, medicamentos o cuidado.

La conexión Health móvil y el gateway son superficies distintas: ChatGPT puede
consultar Health de forma nativa, pero no hay copia automática publicada hacia
el endpoint. La ingestión HTTP es opcional para automatización futura.

Cada observación lleva `provenance_id`, fecha ISO-8601 con offset, unidad y
semántica. Ausencias se rechazan: nunca se convierten en cero.

## Runbook de transición

1. Registrar `POST /health-os/transition` con `started_at` ISO-8601 verificable.
2. Enviar exportaciones Apple Health y, para fuerza, Speediance.
3. Revisar `data_quality(now)`: cobertura, antigüedad, mezcla de legado y sesiones.
4. Tras 14 días locales distintos de datos Apple, cambia a `ERA_APPLE_BASELINE`.
5. Mantener Garmin sólo como archivo histórico de lectura. Cualquier dato
   `GARMIN_LEGACY` posterior a transición deja la calidad en fallo.

## Incidentes y rollback

Detener el productor que genera registros erróneos, conservar la evidencia y
reprocesar con nuevos `provenance_id`; no borrar ni sobrescribir hechos. Para
volver a una interpretación anterior, usar registros históricos y marcar la
era explícitamente, nunca reactivar el puente Garmin de producción.

## Salud y seguridad

Health OS es de apoyo personal, no dispositivo médico ni motor de diagnóstico.
Las conclusiones deben mostrar procedencia y calidad; datos faltantes,
antiguos o en conflicto no habilitan recomendaciones automáticas.
`ACTIVA` no se emite hasta configurar una política de seguridad revisada por un
clínico; los límites físicos de sensor sólo rechazan registros imposibles, no
determinan seguridad médica.
