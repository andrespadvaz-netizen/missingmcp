# Recepción aislada del JSON Tu Anh Do

Estado: implementado y probado localmente sobre la copia de `75b2762`, con
pruebas sintéticas y una integración aislada autorizada del JSON real. No está
publicado ni configurado en producción; no se envió el export al servidor ni
se incorporó al Brief habitual.

## Límite y recorrido

`POST /health-os/ingest/tuanhdo` exige el `HEALTH_OS_INGEST_KEY` existente y
acepta hasta 16 MiB de JSON `aggregation: "raw"` con `records` y `workouts`.
No interpreta el formato distinto `data.metrics` de Health Auto Export.
Conserva el lote completo cifrado con `HEALTH_OS_DATA_KEY` y una fila de
seguimiento por índice de registro. El recibo comunica solamente conteos y
códigos cerrados. Una fila rechazada conserva su código fijo individual
(`unit_unsupported`, `sleep_stage_unsupported`, etc.) sin reflejar valores.
Los entrenamientos permanecen en staging.

La huella derivada sirve para detectar reexportaciones entre lotes; el índice
de staging guarda una versión HMAC protegida con la clave de datos. No es un
UUID de HealthKit. Filas idénticas dentro del mismo lote quedan retenidas.
Si una observación distinta ocuparía la clave actual del core, también queda
retenida; el core tampoco sobrescribe un evento previo con procedencia o
dispositivo distintos. Los intervalos de sueño, pasos, distancia y energía
activa se guardan cifrados en staging. Solapamientos intralote e interlotes
quedan retenidos. Cantidades aditivas puntuales, sin duración, también
quedan retenidas como `additive_point_no_duration`; pulso y peso puntuales
siguen siendo válidos para lectura. Si ya existe evidencia aditiva Apple Health de otra ruta
sin inicio de intervalo recuperable, la nueva métrica queda retenida como
`legacy_interval_unknown` hasta diseñar una reconciliación.

Repetir exactamente el lote devuelve un recibo actualizado con
`replayed: true` sin nuevas escrituras. Un reexport con cabecera distinta
de una huella ya promovida se marca duplicado. Si la huella anterior estaba
retenida, el reexport también queda retenido (`previous_held`); reenviar no
es autorización. `review_held_tuanhdo` permite revisar local y explícitamente
las filas retenidas después de aprobar procedencia: promueve una copia y
marca posteriores copias como duplicadas. No existe ruta HTTP para esta
operación.

Solo se promueve un registro cuando su `source`, `sourceBundleId`,
`deviceDetail.model` y `deviceDetail.softwareVersion` coinciden **exactamente**
con una tupla aprobada en `HEALTH_OS_TUANHDO_WATCH_PROVENANCE`. Esa variable es
un arreglo JSON de arreglos de cuatro cadenas. Vacía o ausente, toda muestra
permanece fuera del core. La coincidencia de metadata no constituye
atestación criptográfica del hardware: un cliente que conozca la clave de
ingesta podría declarar esos campos. Exige una revisión operativa de los
identificadores y del cliente autenticado antes de configurarla. No se copia un nombre libre al campo
`device_source`. Peso se retiene para la fuente manual aprobada por la
Política Operativa v0.5. Los intervalos despiertos, solapados o ambiguos se
retienen. `heartRate` sigue siendo pulso ordinario, nunca FC en reposo.

El parser reconoce estructuralmente los 26 tipos informados para el segundo
JSON; solo promueve los que tienen equivalencia explícita en el core y
procedencia aprobada. RHR y HRV conservan sus tipos propios y no se producen
desde pulso. Los registros sin `deviceDetail` requerido, incluidos el RHR
informado y parte de sueño, quedan retenidos por `provenance_incomplete`.
Saturación y porcentajes de marcha quedan retenidos hasta confirmar su escala
sin inferirla del valor. Categorías y métricas no modeladas se conservan
cifradas con motivos fijos. El Brief conserva sus requisitos de
cobertura por métrica: un historial de sueño sin FC en reposo no completa
Readiness.

El núcleo conserva exactamente su interpretación histórica de `cal` como
`kcal` para no cambiar observaciones almacenadas al volver a leerlas. El JSON
Tu Anh Do declara `kcal` y el parser exige exactamente `kcal`; rechaza `cal`,
`Cal` y otras unidades ambiguas en esta ruta. La semántica histórica de `cal`
es una deuda explícita para una migración o ruta versionada posterior.
El comportamiento histórico del core para saturación con `%` puede interpretar
fracciones; esta ruta retiene ese tipo hasta confirmar la escala del productor.
Antes de cualquier corrección global futura, revisar mediante conteos si la
base histórica contiene `cal`. No se ha inspeccionado ni migrado la base
productiva.

La validacion local cuenta por separado metadata incompleta y motivos de
retencion. Una fila con los cuatro campos completos solo es candidata: la
tupla exacta `(source, sourceBundleId, deviceDetail.model,
deviceDetail.softwareVersion)` debe contrastarse con una muestra conocida
del Apple Watch y con el origen que muestra Salud en el iPhone antes de
aprobarla. No basta que los campos esten presentes ni que un nombre libre
contenga "Watch". Tampoco basta aprobar una tupla para resolver solapamientos,
metricas sin mapeo o intervalos historicos desconocidos.

La ruta HTTP exige ademas `HEALTH_OS_TUANHDO_PROMOTE_TYPES`, un arreglo JSON
de tipos exportados. Vacio o ausente, ningun tipo se promueve aunque coincida
la tupla de procedencia. El alcance por tipo debe aprobarse explicitamente;
la muestra cotejada solo acredita el ensayo de frecuencia cardiaca.

## Evidencia y pendientes de DoD

La prueba sintética recorre HTTP autenticado, cifrado en staging, verificación
de metadata, deduplicación entre lotes, escritura a `HealthOSRepository` y
lectura del Brief. Confirma que 28 días de sueño pueden aparecer en cobertura
mientras FC en reposo permanece en cero. Las regresiones prueban orden inverso,
repetición, concurrencia local, revisión de retenidos y colisiones de la clave
histórica. La suite local completa pasa.

Para cerrar el DoD con Apple Health real falta confirmar en el iPhone una
acción de Tu Anh Do que genere el JSON automáticamente, sus parámetros y su
salida; su oferta Pro anuncia automatización, pero no se ha ejecutado aquí.
Después hay que configurar transporte, credencial y tuplas verificadas con
autorización específica, desplegar con plan de reversión, y probar un recibo
de red y el Brief sin divulgar valores. El archivo anterior solo tenía seis
tipos; el segundo JSON local contiene 26 y se probó en una base temporal
cifrada. Solo 515 muestras de frecuencia cardíaca de la tupla cotejada se
promovieron localmente; 1.490 quedaron retenidas. La repetición no creó filas
nuevas y la base temporal se eliminó. No se envió el JSON a ningún servidor.
Esto aún no acredita cobertura de todos los tipos de Apple Health. Las filas
retenidas requieren la revisión explícita local antes de
promoción. La conciliación de intervalos históricos de otras rutas sigue
pendiente; no se descarta ni fusiona evidencia histórica para forzar el DoD.
