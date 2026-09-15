# Metis Gateway v1 — desplegado, pausado por costo incierto

## PIE y estado

Techo técnico actual: **7/10**. Claude, Railway y el puente Google están conectados.
Las pruebas reales simple y CROSS_AUDIT completaron con respuesta íntegra. Una cuarta
solicitud produjo ENGINE_INTERRUPTED con costo desconocido; la pausa automática
impide nuevos gastos. La causa de la interrupción no está acreditada. No repetir
la solicitud ni levantar la pausa sin reconciliar su resultado y costo.
CANON consultado en vivo: v2.6, 15-ago-2026. La decisión vigente de 31-ago-2026
levanta la moratoria; no autoriza automáticamente componentes con nuevo costo/riesgo.

**Objetivo completo: NO CUMPLIDO. No crear el tag baseline del Gateway todavía.**

## Gap y elección

El motor está acreditado, pero Andrés todavía tendría que operar entradas y salidas
técnicas para usarlo desde una conversación. El Gateway conserva la ejecución y
devuelve su respuesta al cliente. Su beneficio deberá verificarse con uso real;
hasta esa prueba sigue siendo costo de implementación, no MEM demostrado.

Arquitectura elegida para validar:

1. Claude como primer cliente, mediante su conector MCP remoto.
2. Adaptador `/metis/mcp` en el MissingMCP de Andrés ya existente en Railway.
3. Cola y resultados cifrados en SQLite sobre el volumen existente.
4. Proyecto Apps Script **separado**, puente firmado que consume `Engine` versión 5.
5. Engine conserva contexto, recuperación, routing, auditoría, reconciliación,
   proveedores, credenciales, presupuesto y políticas. El puente no los reimplementa.

El Gateway expone sólo `metis_create_execution` y `metis_get_execution`.
La primera llamada devuelve identidad y estado; un worker independiente continúa.
La consulta de estado espera hasta veinte segundos por el resultado, sin bloquear
al worker ni volver a ejecutar modelos. Esto evita que el cliente agote consultas
rápidas y cierre el turno prematuramente mientras el motor sigue trabajando.
No hay modelo de routing nuevo, vector DB, nueva base de datos gestionada ni triggers.

### Alternativas evaluadas

| Alternativa | Resultado |
|---|---|
| Una llamada síncrona desde GPT Actions | Su límite de 45 segundos no cubre corridas de varios minutos. |
| Apps Script Execution API directa | Exige proyecto Cloud estándar compartido con el cliente OAuth; no se acreditó esa configuración. |
| Todo en Apps Script con Properties y triggers | Límites de almacenamiento y programación menos adecuados para historial durable. |
| Reusar Railway + puente a biblioteca v5 | Reutiliza OAuth, TLS, proceso persistente y volumen; preserva motor. Falta validar biblioteca en vivo. |

### Infraestructura verificada

- Railway: proyecto `heartfelt-healing`, servicio `missingmcp`, volumen `missingmcp-volume`.
- URL actual: `https://missingmcp-production.up.railway.app`.
- Una réplica; serverless deshabilitado; auto deploy desde `main`.
- Despliegue anterior: `10810a56-6843-4cfe-993a-2ac606359c59`.
- No se modificó configuración ni se creó servicio de pago.
- Apps Script v5 descargado y comparado: 38 archivos coinciden con baseline.

## Contrato de ejecución

Crear acepta exclusivamente `request` (1–16000 bytes UTF-8) e `idempotency_key`
(16–128 caracteres ASCII de letras, dígitos, guion o guion bajo). La misma clave
y el mismo texto devuelven la misma ejecución; texto distinto con la misma clave
produce conflicto. El usuario no selecciona modelo ni eleva límites o autoridad.

Estados: `QUEUED` → `DISPATCHED` → `COMPLETED`, `FAILED` o `REQUIRES_ANDRES`.
La identidad UUID del Gateway también es el `correlation_id`. El resultado conserva
por separado `engine_execution_id`, `engine_status`, ruta, costo conocido/desconocido,
límites, auditoría, handoff, degradación y el `final_answer` íntegro.

El origen lógico de este primer cliente se declara en servidor como `ANTHROPIC`.
El motor corre con LEVEL_2 temporal (escrituras simuladas) y restaura LEVEL_0.
No se añade override a `METIS_LIMITS`.

### Reintentos y cortes

- SQLite confirma el registro antes de enviar al puente; cliente y worker no comparten vida útil.
- El puente mantiene un número de secuencia monotónico y una huella SHA-256.
- Confirma su recibo antes de llamar al motor y mantiene el bloqueo durante la ejecución.
- Una secuencia repetida devuelve su resultado; una antigua jamás vuelve a ejecutar.
- El puente retiene sólo el resultado más reciente, comprimido y dividido en propiedades.
  El Gateway lo guarda durablemente antes de enviar la siguiente secuencia.
- Una consulta de estado que llega antes que una solicitud retrasada cierra esa secuencia
  como `DISPATCH_NOT_DELIVERED`, con costo cero; la solicitud retrasada queda bloqueada.
- Si el proceso Google terminó sin resultado, devuelve `ENGINE_INTERRUPTED`, costo
  desconocido y revisión requerida. No se inventa un ID del motor.
- Si no se puede reconciliar durante 15 minutos, `FAILED/TRANSPORT_UNCERTAIN` pausa
  persistentemente nuevas corridas. Las ya en cola muestran `gateway_paused`.
- No existe reintento automático de una corrida pagada. Una revisión humana es necesaria
  para resolver una pausa, cotejar recibo/costos y recuperar el resultado antes de reabrir.
- Nunca borrar/reiniciar secuencias ni restaurar SQLite a un punto anterior sin reconciliar
  el recibo del puente. Ante una diferencia se falla cerrado.

## Seguridad y límites

- Reutiliza OAuth 2.1, PKCE y límites del proxy. Los tokens son específicos del adaptador.
- La clave de conexión es exclusiva de Metis; no es una clave de proveedor.
- La firma servidor→puente usa otra clave de al menos 32 caracteres, HMAC-SHA256
  y ventana temporal de 90 segundos. El puente rechaza ausencia, firma o huella inválidas.
- La URL pública de Apps Script sería accesible desde Internet, pero **no ejecuta** sin firma.
- La clave de cifrado existente, la de conexión y la de firma deben ser distintas.
- Las credenciales de OpenAI, Anthropic, Notion y Asana permanecen en la biblioteca.
- Solicitudes y resultados Railway cifrados AES-GCM. El caché Google usa protección
  de acceso de Script Properties, sin una capa de cifrado propia adicional.
- No se envían argumentos, respuestas ni metadata suministrada por el cliente a PostHog.
- Ingreso: máximo cinco pendientes y veinte nuevas solicitudes por hora; además de
  los límites económicos del motor. Duplicados no consumen esas cuotas nuevamente.
- Resultado del puente: máximo 360000 caracteres comprimidos/base64. Si lo excede,
  falla explícitamente y pausa; nunca presenta una respuesta truncada como completa.
- La cola conserva historial y recibos; no hay purga automática. Una política de retención
  deberá preservar recibos, ser autorizada y no borrar evidencia antes de acreditar el DoD.

## Despliegue — autorizado, en validación

Andrés autorizó expresamente crear y publicar el puente con sus permisos de lectura
y llamadas externas. Se resolvió el bloqueo anterior de aprobación.

- Proyecto creado: `1kO_QM5HgBgG10O1wCTIuqeHgB_GfFRrPXyNnGf4_xr6LrzNsuZbchMBZ`.
- Puente publicado v1: `AKfycbzQsyCh1k2aGCib_8TVBbWvrGCNcyAUZiGbg2Ho-JoDC6w16jMaBuQfzdvzJyUp_Typ`.
- Prueba en Google: engine_version=5, LEVEL_0, providers_ready=true.
- Prueba HTTP real sin firma: responde `unauthorized`, sin ejecución del motor.
- Claves iniciales generadas en Script Properties, sin escribir valores en código/logs.
- Railway activado: deployment `100fc746-584f-4691-8ced-67b1d6efa3ec`.
- Claude conectado; [conversación de aceptación](https://claude.ai/chat/d18ef5b9-a36f-45dc-b7e4-54f976d091d9).
- Puente actualizado a v2: corrige el tipo MIME necesario para descomprimir el caché.
  El recibo y resultado original se recuperaron sin repetir el motor.
- Primer execution_id `1e5eeb49-1dde-4ef6-a967-768e536e4b4e`; engine_execution_id
  `39bfe82f-adaa-4a34-8373-fef246f28983`. REQUIRES_ANDRES / engine UNCERTAIN /
  ABSTAIN, por no declarar contexto en la solicitud. Costo conocido $0, cero llamadas
  y cero intervenciones. Claude mostró la respuesta completa; no cuenta como éxito simple.
- Reintento real de create con mismos argumentos y clave devolvió el mismo ID,
  timestamp y resultado tras reinicio del Gateway; no produjo otra ejecución.
- Caso simple aclarado: `ffdcb44f-59e9-4f37-b5c7-1a880fbb0b89`, motor
  `96dcbd0d-0c0d-43b8-a93d-9119206d211a`, COMPLETED / LOCAL, una intervención,
  cero lecturas/escrituras, costo conocido US$0.032585. Respuesta completa en Claude.
- Hallazgo: el logger HTTP heredado guardaba URLs temporales de respuestas de Google.
  Corregido con supresión limitada al contexto de transporte Metis y prueba de regresión;
  no se registraron claves de proveedor. El primer resultado sólo contenía abstención.

Acción concreta autorizada: crear `Metis Orchestration Gateway v1 — bridge` en la cuenta
Google de Andrés, subir los dos archivos revisados y vincular biblioteca v5. Después,
autorizar su ejecución con acceso de lectura a Drive/Calendar y llamadas externas,
configurar firma secreta y publicar el puente autenticado por HMAC. Esto amplía la
superficie de invocación del motor; no cambia el código ni los límites del motor.
No se solicita un nuevo servicio de pago ni ampliar presupuesto de modelos.

Pasos tras aprobación:

1. `node scripts/metis-bridge-project.cjs` crea/reutiliza sólo el proyecto separado,
   sube código y verifica lectura posterior. Rechaza el ID del motor.
2. Ejecutar `inspectBridge` en Google; acreditar acceso a la biblioteca y presencia
   de credenciales sin leer sus valores ni efectuar llamadas pagadas.
3. Configurar `GATEWAY_BRIDGE_SECRET` en las propiedades del puente; nunca en Git/logs.
4. Autorizar y publicar una versión inmutable del puente. Registrar ID, versión y URL.
5. En Railway configurar `METIS_BRIDGE_URL`, `METIS_BRIDGE_SECRET` y `METIS_OPERATOR_KEY`
   con el gestor de variables, y verificar volumen, ruta real de SQLite y respaldo.
6. Desplegar la rama revisada manteniendo una réplica y los otros adaptadores.
   No cambiar la rama de producción ni promover código sin revisar el diff completo.
7. Conectar Claude mediante OAuth. Andrés completa login/MFA cuando se solicite.
8. Probar caso natural simple y después CROSS_AUDIT; recuperar desde el cliente
   la respuesta completa, IDs, costo y reconciliación. Probar corte/reintento sin nueva ejecución.
9. Registrar evidencia real. Sólo entonces evaluar DoD y tag final.

### Rollback

Detener ingreso Metis retirando sus tres variables y volver al despliegue Railway
anterior verificado. Conservar SQLite y recibo Google para reconciliar cualquier
ejecución pendiente antes de reactivar. No restaurar datos antiguos como rollback
de código. Desactivar la publicación del puente cuando ya no haya corrida activa.
Motor v5 y otros despliegues Google permanecen intactos.

## Validación y límites de la evidencia

- CI Linux del código desplegado `85cf8b6`: **345 pruebas Python PASS**, 9 del puente
  PASS, motor 168 tests / 898 assertions PASS y 9 guardas PASS.
  [Ejecución acreditada](https://github.com/andrespadvaz-netizen/missingmcp/actions/runs/34928446993).
- Ningún archivo de `metis-orchestrator-prototype` modificado. Biblioteca Google v5.
- Simple real COMPLETED / LOCAL: Gateway `ffdcb44f-59e9-4f37-b5c7-1a880fbb0b89`,
  motor `96dcbd0d-0c0d-43b8-a93d-9119206d211a`, costo conocido US$0.032585,
  una intervención, cero herramientas. Respuesta completa recibida en Claude.
- CROSS_AUDIT real COMPLETED: Gateway `07b1f464-08b1-4c52-b7e6-02b7ba6eef74`,
  motor `266df533-d272-476a-9655-7c960309c271`, costo conocido US$0.36720625,
  9/9 intervenciones, 21/32 llamadas a herramientas, cero reintentos de lectura.
  Handoff ANTHROPIC → OPENAI reconciliado; respuesta final de 3436 caracteres
  conservada en el resultado de la herramienta. Auditoría bloqueante: rechaza
  20 lecturas y confirma 15; cuarto turno sólo de cierre. Sin cambios externos.
- Persistencia e idempotencia reales: repetir el primer create con misma clave y
  argumentos después de un redespliegue devolvió mismo ID, fecha y resultado;
  ninguna nueva ejecución del motor.
- El cliente inicialmente terminaba antes de recibir CROSS_AUDIT. Se añadió espera
  de hasta 20 segundos por consulta de estado, conservando el mismo ID. La prueba
  natural posterior no acredita esa mejora de extremo a extremo: se interrumpió.
- Incidente pendiente, secuencia 4: Gateway `5e151e04-02f3-42b1-8464-b5b8370a27ad`,
  clave `METIS_GATEWAY_AUDIT_20260914_02`, FAILED / ENGINE_INTERRUPTED,
  `cost_known=false`, `cost_usd=null`, sin ID del motor ni respuesta final,
  `requires_review=true`, Gateway pausado. Google conserva el mismo error en su
  recibo DONE. Su historial mostraba Running para el inicio 14-sep 22:23:21 México,
  incluso con duración superior a siete minutos. Cloud logs y Cloud errors no
  están disponibles en esa vista. Esto no permite atribuir una causa concreta.
- Costos conocidos de esta aceptación: **US$0.39979125 más el costo desconocido
  de la secuencia 4**. No presentar el subtotal como costo total.
- El ledger del motor guarda contadores agregados; no acredita por sí solo el
  costo de una llamada cuya respuesta pudo perderse.
- No hubo repetición automática de la solicitud fallida. No se levantó la pausa,
  no se modificaron límites y no se creó el tag final del Gateway.
- Pendiente: reconciliar secuencia 4 con evidencia de resultado/costo, diagnosticar
  la interrupción, acreditar flujo natural completo y volver a evaluar DoD A–P.
- Revisión de permanencia: cuatro semanas después de acreditar el primer cliente;
  retirar si no reduce operación técnica de Andrés o su mantenimiento supera valor.

## Fuentes

- [GPT Actions: producción](https://developers.openai.com/api/docs/actions/production)
- [Claude: conectores remotos](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp)
- [Google: bibliotecas y recursos compartidos](https://developers.google.com/apps-script/guides/libraries)
- [Google: límites](https://developers.google.com/apps-script/guides/services/quotas)
- [Google: Execution API](https://developers.google.com/apps-script/api/how-tos/execute)
- [Google: crear proyecto](https://developers.google.com/apps-script/api/reference/rest/v1/projects/create)

## Fila de Chat Log propuesta — todavía no registrada

| Tema | Fecha | Modelo | Proyecto | Estado | Link |
|---|---|---|---|---|---|
| Gateway v1: simple y CROSS_AUDIT aprobados; pausa por ejecución con costo incierto | 2026-09-14 | ChatGPT | Metis | En curso | https://github.com/andrespadvaz-netizen/missingmcp/tree/metis/orchestration-gateway-v1 |

No hay decisión canónica ni acreditación nueva registrada. Este texto documenta progreso.


## Conciliación del incidente 4 — 14 septiembre, 23:25 México

Se exportó OpenAI por minuto para 15-sep UTC: 1440 filas; sólo cuatro solicitudes,
una a las 04:18 y tres a las 04:19. Ninguna desde las 04:23. Anthropic registra
`req_011Cf4b2ZzVN6kukcNnHX2Rn` a 04:23:58.784 UTC: éxito, Opus 5, entrada 2450,
salida 2405, caché cero, nivel estándar, 32.987 segundos. Andrés confirmó que no
hubo otro uso de las claves en la ventana 22:23–22:25 México.

La estimación verificable es `(2450*5 + 2405*25)/1000000 = US$0.072375`, según
[tarifa oficial](https://platform.claude.com/docs/en/about-claude/pricing).
La facturación diaria de Anthropic todavía mostraba cero; no es una factura final.
Google cambió el estado de la ejecución a UNKNOWN: no puede determinar su desenlace.
El motor no había sumado el gasto: diario0.39979125, mensual5.1390775.

Se agregó una operación manual limitada al recibo, fecha, contadores y evidencia
de este incidente. Persiste intención antes del ajuste, comprueba ambos contadores
y admite recuperación tras interrupción sin volver a sumar. Un estado parcial o
conflictivo conserva el bloqueo. No se expone en MCP ni en doPost. El motor v5 no
cambia; sólo se ajustan sus contadores operativos mediante Ledger.addSpend.

El Gateway puede consultar un recibo revisado mientras está pausado; nunca vuelve
a ejecutar el motor. Sólo libera la pausa con evidencia identificada por hash,
ID y secuencia coincidentes y ledger verificado. Conserva el fallo original cifrado,
el estado FAILED y la ausencia de respuesta final. Una conciliación no convierte
la solicitud en éxito. Pruebas específicas:31 Python y12 puente PASS.

Evidencia local íntegra: `.localdata/reconciliation-seq4.json` (excluida de Git),
SHA256 `8ebe9f1018bb9766dd073ec8bbadd70f681bae9bb3f0353f38a6eba40f806571`.
No incluye claves secretas ni el corpus recuperado.
