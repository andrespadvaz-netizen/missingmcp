# Auditoría demoledora del Orchestration Gateway

Fecha: 2026-10-11  
Estado: diagnóstico técnico; no acredita DoD productivo  
Commit inspeccionado: `61df44f`

## Declaración Anticipada PIE

- **Propósito:** reconstruir todos los modos de fallo observados y buscar sus variantes futuras antes de otra corrida pagada.
- **Independencia:** revisión adversarial del repositorio, historial Git, pruebas locales y evidencia operativa disponible. No se presupone que una corrección desplegada funciona por existir en Git.
- **Evidencia:** hechos verificados se distinguen de riesgos inferidos. No se ejecutaron modelos ni escrituras externas.
- **Techo técnico:** **8/10**. El código y 26 pruebas del puente fueron inspeccionados; faltan caos real de red/proveedor, verificación del volumen Railway y aceptación extremo a extremo del despliegue corregido.
- **Sesgo declarado:** esta auditoría favorece disponibilidad, recuperabilidad y observabilidad. Puede penalizar controles que fallan cerrado si convierten incidentes rutinarios en intervención frecuente de Andrés.
- **CANON:** no se cargó el CANON vigente desde Drive en esta corrida. El espejo local contiene una copia antigua y no se usó como autoridad. Este documento no cambia gobernanza ni CANON.

## Dictamen

**RECHAZAR la declaración de “100% funcional” y congelar nuevas funciones.**

El sistema ha demostrado funciones reales, pero su arquitectura de recuperación todavía depende de una contradicción: Railway maneja pausas por contexto y conserva historial, mientras Apps Script conserva un único recibo global y sólo el último resultado. Una ejecución posterior puede borrar la única evidencia automática necesaria para reconciliar una anterior. Además, una llamada individual al proveedor puede superar el límite duro de Apps Script; el parche de 4,096 tokens reduce la probabilidad, pero no impone un timeout al proveedor ni elimina el límite estructural.

## Recuento de fallos ya observados y corregidos

| # | Fallo observado | Causa raíz | Corrección existente | Riesgo residual |
|---|---|---|---|---|
| 1 | OAuth terminó en `localhost:8888` rechazado | callback CLI sin listener disponible | copiar callback o renovar sesión CLI | reautenticación corporativa sigue siendo manual |
| 2 | contexto ausente / `UNSCOPED` | el cliente no transmitía contexto conversacional | sobre estructurado `context` obligatorio | el cliente puede enviar raíz en vez de subproyecto |
| 3 | ID visible `.Final_Final` rechazado | nombre humano confundido con ID técnico | canonicalización `FINAL_FINAL` | registro duplicado entre Gateway, Engine y Notion |
| 4 | `PERSONAL / Health OS` no resuelto | espacios y subproyecto no soportados consistentemente | normalización y raíz heredada | el motor colapsa subproyectos a la raíz y pierde aislamiento fino |
| 5 | runtime aparentemente inerte | comentarios y nivel efectivo divergentes | nivel se fija sólo durante ejecución | documentación todavía puede desalinearse del despliegue |
| 6 | fuentes sin cobertura | particiones de lectura ausentes o vacías no verificadas | provisión y canarios por contexto | credencial, ID o permiso puede caducar sin preflight rutinario |
| 7 | Google Docs API desactivada | servicio avanzado no habilitado | servicio añadido | scopes/servicios pueden divergir entre versión y despliegue |
| 8 | lectura de auditoría falló silenciosamente | fallos de herramientas no llegaban al recibo | `AUDIT_READ_FAILURES` expuesto | error aún puede ser genérico y no señalar fuente/etapa suficiente |
| 9 | salida vacía de proveedor | proveedor terminó sin texto utilizable | recuperación acotada | puede consumir otra intervención y presupuesto |
| 10 | respuesta truncada por longitud | techo de salida y reconciliación insuficientes | continuaciones ancladas | una sola llamada de 16k excedió el runtime; parche 4k aún no desplegado |
| 11 | presupuestos bloquearon aceptación | techos por corrida/día/mes inferiores al caso | límites autorizados y preflight | configuración viva puede desalinearse del reporte o facturación |
| 12 | precio desconocido | modelo nuevo sin tabla vigente | fail-closed y tabla externa | una actualización de modelo puede detener producción |
| 13 | módulos ausentes en despliegue | push parcial del proyecto Apps Script | despliegue de todas las fuentes | no existe verificación automática de paridad repo↔Apps Script |
| 14 | pin de biblioteca obsoleto | motor y puente versionados por separado | alineación manual del pin | metadata duplicada permite afirmar versión incorrecta |
| 15 | respuesta Google no recuperable | MIME incorrecto al descomprimir caché | MIME corregido | almacenamiento por fragmentos no es transaccional |
| 16 | el cliente dejó de consultar pronto | polling corto frente a trabajo largo | espera/polling ampliado | la experiencia sigue cortando el flujo durante minutos |
| 17 | triggers tardaron minutos u horas | scheduler de Apps Script no ofrece latencia inmediata | warm worker y `advance` desde Railway | el camino directo vuelve síncrona una llamada larga |
| 18 | cuota/deadlock de triggers | triggers viejos acumulados | poda antes de programar | carreras entre trigger y worker siguen dependiendo de un lock global |
| 19 | `ENGINE_INTERRUPTED` con costo desconocido | Apps Script murió después del despacho | recibo durable STARTED/ACCOUNTED | STARTED no incluye request ID ni uso; conciliación sigue manual |
| 20 | pausa bloqueó trabajo posterior | resultado incierto exige revisión | pausa por contexto | el recibo de Apps Script sigue siendo global, no por contexto |
| 21 | reserva contable presentada como costo conocido | conciliación conservadora mezclada con gasto real | campo `basis` | agregadores que ignoren `basis` sobrestiman gasto facturado |
| 22 | escritura incierta | corte tras mutación y antes del recibo | readback de objeto conocido | escrituras tienen una pausa global, aunque las lecturas sean por contexto |
| 23 | resultado completo no cabía | límite de Script Properties | compresión y fragmentación | 360k base64 y cuota total siguen siendo frontera dura |
| 24 | actualización/repetición bloqueada por cliente | controles externos antes del Gateway | rediseño y pruebas por cliente | capacidad depende de políticas cambiantes de Claude/ChatGPT |
| 25 | auditoría v68 falló a 369 s | `advance` ejecutó proveedor dentro de `doPost` y excedió runtime | parche local a 4,096 tokens + duración por tramo | aún no desplegado; reducir tokens mitiga, no elimina el límite |

## Hallazgos nuevos

### P0 — El recibo global contradice las pausas por contexto

**Hecho verificado.** `Bridge.gs` guarda `receipt`, `durable_*` y `result_*` sin ID de contexto y declara que sólo conserva el último resultado. Railway permite saltar un contexto pausado y procesar otro. Cuando llega una secuencia mayor, el resultado anterior se vuelve `GONE` y se eliminan sus fragmentos.

**Impacto:** una corrida posterior puede hacer imposible la conciliación automática de la anterior. El operador queda obligado a revisar proveedores y reservar costo manualmente.

**Corrección requerida:** recibos por `execution_id`/`seq` con índice monotónico y retención acotada; nunca sobrescribir una ejecución `requires_review=true`. Alternativa preferida: mover el almacén durable al SQLite cifrado de Railway y dejar Apps Script sin estado global.

### P0 — Apps Script no es un runtime seguro para esperar llamadas largas

**Hecho verificado.** La ejecución v68 de `advance` duró 369.414 s y terminó `Timed Out`. Railway esperaba 120 s. El proveedor quedó STARTED sin recibo terminal.

**Impacto:** costo desconocido, pausa e intervención humana aun con idempotencia correcta.

**Corrección requerida:** ejecución asíncrona de proveedor fuera de Apps Script. Railway debe persistir el intento, despachar/pollear al proveedor y entregar al motor una respuesta normalizada. El límite de 4,096 tokens es sólo contención temporal.

### P0 — Recuperación codificada incidente por incidente

**Hecho verificado.** `_INTERRUPTED_RECEIPTS` contiene IDs y secuencias específicos en código. Cada incidente nuevo exige editar, desplegar y confiar en un hash manual.

**Impacto:** el sistema no es autónomo; la recuperación es operación artesanal y no escala.

**Corrección requerida:** tabla cifrada de conciliaciones con comando administrativo autenticado, evidencia firmada, doble estado `estimated/reserved/invoiced` y auditoría append-only. Ningún ID de incidente en el código.

### P0 — Clases de fallo incompatibles con la recuperación

**Hecho verificado.** `reconcile_terminal` sólo acepta un fallo local cuyo error sea `TRANSPORT_UNCERTAIN`. Los fallos `ENGINE_INTERRUPTED`, `ENGINE_WORKER_INTERRUPTED` y `DURABLE_PROVIDER_UNCERTAIN` no pueden convertirse automáticamente en un recibo terminal firmado.

**Impacto:** puede existir evidencia terminal válida y aun así permanecer la pausa.

**Corrección requerida:** máquina de estados formal que acepte evidencia más fuerte para cualquier estado incierto, sin cambiar un fallo determinista ni repetir proveedor.

### P0 — Aislamiento de subproyectos incompleto

**Hecho verificado.** Gateway conserva `PERSONAL/HEALTH_OS`, pero `ContextResolver.registeredRoot()` devuelve `PERSONAL`; las particiones y el resultado del Engine operan sobre la raíz. La suite productiva además falla para un hijo registrado porque el matcher devuelve padre e hijo.

**Impacto:** mezcla potencial de fuentes entre subproyectos de una misma raíz o abstenciones inconsistentes.

**Corrección requerida:** registro canónico de IDs completos, resolución longest-match, particiones explícitas por subproyecto y prohibición de herencia implícita para sustancia.

### P1 — Telemetría de duración se borraba al completar (corregido localmente)

**Hecho verificado.** el primer parche local guardaba `duration_ms` dentro del estado durable, pero `finish_()` no lo copiaba al resultado y después borraba el durable. Este barrido añadió `provider_checkpoint_timings` al recibo terminal y su regresión.

**Impacto:** no se puede medir producción ni atribuir latencia después de una corrida exitosa.

**Pendiente:** desplegarlo y completar métricas no sensibles por etapa: queued→dispatch, bridge ACK, retrieval, cada proveedor, reconciliación y total.

### P1 — Errores operativos se silencian

**Hecho verificado.** `Worker.run()` captura `Exception` y no registra ni clase, etapa, contador o health flag. Apps Script también convierte excepciones amplias en `ENGINE_OR_PERSISTENCE_FAILURE`.

**Impacto:** un bug permanente parece una espera normal; no hay alarma ni diagnóstico accionable.

**Corrección requerida:** telemetría estructurada sin sustancia: clase permitida, etapa, correlación, contador, edad y última transición; health endpoint y alerta por SLO.

### P1 — Escrituras pausadas globalmente

**Hecho verificado.** `metis_write_pause` se consulta sin contexto. Una escritura incierta impide todas las posteriores.

**Impacto:** un destino bloquea proyectos no relacionados.

**Corrección requerida:** pausa por `(context,destination,object_id)`; sólo elevar a global cuando exista corrupción del ledger o credencial compartida comprometida.

### P1 — Versiones declaradas en varios lugares

**Hecho verificado.** manifest, `inspectBridge`, resultado y documentación contienen versión/release manuales.

**Impacto:** recibos pueden afirmar motor distinto al ejecutado.

**Corrección requerida:** un `BUILD_ID` generado al desplegar; puente valida el pin y lo copia del motor en runtime.

### P1 — Escritura multi-fragmento no transaccional

**Hecho verificado.** resultado y durable se escriben en múltiples Script Properties; se borran fragmentos previos antes de publicar el nuevo puntero.

**Impacto:** corte intermedio produce recibo ilegible y elimina la copia anterior.

**Corrección requerida:** copy-on-write con generación, hash y puntero final; conservar generación anterior hasta validar lectura.

### P1 — La cola puede parecer viva mientras está inmóvil

**Inferencia alta.** un DISPATCHED antiguo se selecciona primero; BUSY/timeouts se absorben y el worker reintenta sin progreso visible hasta el umbral de 15 minutos.

**Corrección requerida:** lease explícito, `next_attempt_at`, contador de BUSY, edad por etapa y terminalización diferenciada.

### P2 — Retención ilimitada en SQLite

**Hecho verificado.** historial y recibos no tienen purga automática.

**Impacto:** crecimiento, backup lento y eventual indisponibilidad del volumen.

**Corrección requerida:** política autorizada de retención que preserve hashes/contabilidad y archive payload cifrado.

### P2 — Dependencia de credenciales y permisos sin preflight

**Inferencia alta.** API keys, OAuth, scopes, servicios avanzados y particiones cambian fuera del código.

**Corrección requerida:** preflight barato sin modelo: build/pin, credenciales presentes, permiso mínimo por raíz, cuotas de propiedades/triggers/DB y salud del proveedor.

## Pruebas ejecutadas en este barrido

- Puente: **26/26 PASS**.
- Contrato Lens: **PASS**.
- Guardas estáticas: **9/9 PASS**.
- Terminal y recuperación: **20/20 PASS**.
- Suite productiva: inicialmente reprodujo el fallo de hijo registrado; tras corregir exact/longest-match, **26/26 PASS** sin red.
- Pytest Gateway: no produjo resultado terminal en 60 s y fue interrumpido; no se cuenta como PASS.
- Ninguna llamada de modelo ni escritura externa.

## Definition of Done revisada

No se debe volver a declarar DoD con una sola corrida feliz. El DoD exige:

1. Recibos durables independientes para dos contextos concurrentes; pausar A y completar B sin perder A.
2. Recuperar A con recibo terminal firmado tras procesar B.
3. Cero IDs de incidentes codificados en producción.
4. Subproyecto exacto conservado desde cliente hasta particiones y recibo.
5. Prueba de proveedor lento mayor al timeout HTTP sin costo desconocido ni pausa manual.
6. Corte en cada transición durable: antes de despacho, durante proveedor, después de respuesta, durante contabilidad y durante fragmentación.
7. Prueba de pérdida/reinicio de Railway y Apps Script con el mismo execution/idempotency key.
8. Prueba de corrupción y capacidad: SQLite, Script Properties, resultado máximo y cuota de triggers.
9. Matriz de errores completa: cada error tiene etapa, costo, reintento permitido/prohibido y recuperación automática.
10. Métricas terminales de tiempos y SLO: ACK p95 < 3 s; estado visible < 10 s; auditoría rutinaria p95 < 180 s; cero esperas mudas > 30 s.
11. Dos corridas reales consecutivas por cada ruta: LOCAL, CROSS_AUDIT, lectura, escritura create/update y readback incierto.
12. Cliente Claude y cliente ChatGPT recuperan el mismo resultado sin intervención manual.
13. Despliegue reproducible prueba repo→motor→pin→puente→Railway y emite un solo BUILD_ID.
14. Runbook de conciliación no exige editar código ni inspeccionar secretos.
15. Observación de siete días o 30 ejecuciones reales, lo que ocurra primero: ≥95% terminal sin Andrés, cero duplicados, cero mezcla de contexto y contabilidad completa.

## Orden de reparación

1. **Detener nuevas funciones.** Corregir P0 antes de más aceptación pagada.
2. Mover ejecución de proveedor y recibos durables a Railway o adoptar un job asíncrono equivalente.
3. Sustituir recibo global de Apps Script por recibos por ejecución con copy-on-write.
4. Formalizar máquina de estados y conciliación genérica.
5. Registrar subproyectos y particiones exactas; reparar longest-match.
6. Exponer telemetría/SLO y eliminar excepciones silenciosas.
7. Ejecutar caos determinista y luego aceptación real cronometrada.

## Conclusión

El problema dominante ya no es “un bug nuevo cada vez”. Es que el sistema distribuye estado crítico entre SQLite y un único recibo de Apps Script, mientras ejecuta trabajo potencialmente más largo que el runtime que lo hospeda. Los parches actuales han reducido fallos particulares, pero no eliminan esa incompatibilidad. Resolverla es condición previa para afirmar que el Orquestador reemplaza al PHI manual en vez de añadir fricción operativa.
