# Metis → Asana: creación, evaluación y revisión

El único Dot habilitable por este flujo es Metis. `POST /asana-dot/event` exige
la credencial de servidor `METIS_OPERATOR_KEY` y el OAuth saliente de Asana.
El endpoint permanece deshabilitado sin `ASANA_METIS_DOT_POLICY_JSON` y
`ASANA_AGENT_PROFILES_JSON`. Esas variables se configuran fuera del repositorio;
no se derivan del evento. La política fija `workspace_gid`, `creator_gid`,
`project_gid`, `context`, `owner_gid`, `eligible_task_names`, `routing`
(nombre de tarea → especialidad autorizada), `specialties`,
`audiences` y `sources`. Los perfiles de agentes fijan por GID sus
`specialties`, `contexts`, `audiences` y `dot_authorized`. Meli y Val no usan
esta clave ni esta política.

`owner_gid` es el responsable humano de la tarea; no tiene que ser el dueño
del proyecto. Por defecto, el proyecto debe ser privado y pertenecer al
workspace fijado.
`project_owner_gid` puede fijarse por separado para una futura autorización
de despachos; si falta, el control de destino también bloquea ese despacho.
Para un proyecto visible a todo el workspace, la política debe declarar
explícitamente `privacy_setting: public_to_workspace` y solo la audiencia
`workspace`; el Gateway comprueba ese valor real antes de crear la tarea.
La política actual que espera `private` no puede usar un proyecto público por
accidente. Un proyecto público no autoriza fuentes privadas ni agentes.

## Crear y evaluar

El emisor autorizado envía `task.create` con `idempotency_key` estable,
`name` y `brief`. El Gateway reserva la clave
en SQLite **antes** de enviar el POST a Asana. El POST crea la tarea en el
proyecto permitido con `owner_gid` como responsable humano y una referencia
estable en las notas, junto con el encargo y las fuentes declaradas para la
audiencia permitida. La herramienta MCP expone desde la política del servidor
el contexto, proyecto, audiencia y fuentes admitidos. Después lee la tarea
desde Asana y comprueba nombre,
proyecto, responsable y creador esperado. Solo entonces registra su GID y
evalúa el brief. El Dot no necesita conocer el GID antes de crearla: el
Gateway añade el GID verificado al brief para evaluar y conserva la huella
del brief original. Audiencia, contexto, proyecto y fuentes se validan antes
del POST. `created_by` es una comprobación adicional: la prueba de
origen es la reserva previa, el POST del servidor y la lectura posterior.
Un evento `task.created` externo solo puede evaluar un GID registrado por
esa secuencia. Una repetición con la misma clave no hace otro POST; un
cambio de contenido causa conflicto.
Un nombre fuera de `eligible_task_names` todavía puede crear la tarea humana,
pero la evaluación se abstiene de encargarla a un agente. Una lista vacía
permite activar solo la creación y la abstención, sin habilitar especialidades.
Un brief sin solicitud, entregable o fuente autorizada se rechaza antes del POST.

Si la respuesta del POST se pierde, la clave permanece incierta y no se
reenvía. `task.source_reconcile` con la misma clave busca la referencia en
Asana; solo una coincidencia verificada permite completar el registro. Si
la búsqueda no encuentra exactamente una tarea, hace falta conciliación
humana. La evaluación y el encargo separado usan la misma protección contra
duplicados; `task.reconcile` comprueba un despacho incierto sin reenviarlo.
La tarea original nunca se reasigna al agente. El brief debe aportar
audiencia, fuentes permitidas, entregable y petición; la decisión de valor
y la especialidad salen de `routing` y del nombre comprobado en Asana, no
de afirmaciones del evento. El servidor se abstiene ante datos insuficientes,
una ruta ausente o una elección ambigua de agente.

## Costo compartido: despacho bloqueado

La consola real de Venture Quest informa **50 solicitudes adicionales por
ciclo, MXN 11 cada una, máximo MXN 550**, compartidas entre Dash y los
agentes. El conector disponible no permite leer ni reservar el saldo global.
La pantalla de administración «Gestionar el uso bajo demanda» muestra que
Asana tiene activo «Establece un límite de gasto» en 50 solicitudes por ciclo
y afirma que dejará de ejecutar solicitudes al llegar a ese límite. Es un
tope aplicado por Asana a la cuenta, no una lectura automática disponible
para el Gateway; puede cambiarse en la consola. Las solicitudes bajo demanda
empiezan después de agotar las incluidas y prepagadas.

Por ello el flujo automático registra `shared_quota_unavailable` y **no
despacha agentes pagados**. Un importe, un saldo declarado en el evento o
una bandera de política no elimina este bloqueo. `scripts/asana_agents.py
evaluate` permite una evaluación local sin escritura; `--dispatch` está
bloqueado. La creación de la tarea humana en Asana sigue siendo una operación
distinta del despacho pagado del agente.

La [especificación pública de Asana](https://github.com/Asana/openapi/blob/e5c7dec74dcea63207156a7cc96d06f82075a1d3/defs/asana_sdk_oas.yaml)
documenta `GET /workspaces/{workspace_gid}/ai_studio/runs`, restringido a
cuentas de servicio de organizaciones con AI Studio. Devuelve créditos de
ejecuciones de reglas de AI Studio. No demuestra el saldo de solicitudes
adicionales de AI Teammates compartido con Dash; no se usa para levantar el
bloqueo.

Para levantar el bloqueo se necesita una lectura autorizada y vigente del
saldo global **y** una reserva atómica compartida con Dash y los agentes,
incluida conciliación de solicitudes inciertas. Una opción es un servicio
único que contabilice todas las solicitudes de Dash y agentes y consulte la
consola oficial al iniciar cada ciclo; otra es una API oficial de cuota si
Venture Quest la ofrece. Una captura o un contador solo del Gateway no basta.
El cambio de código que use ese control requiere pruebas de concurrencia y
no está activado aquí.

## Propuesta y revisión

Si ya existe un encargo autorizado, `task.proposal` registra el GID de un
comentario no vacío de su agente en Asana como propuesta pendiente. Luego
una persona con `owner_gid` deja un comentario exactamente `METIS_APPROVE`
o `METIS_REJECT` después de la propuesta; `task.review` verifica su autor,
fecha y secuencia y registra el comentario,
la decisión y el revisor. La propuesta no aprueba ni cierra la tarea humana.

Para revertir la entrada, retire la política o desconecte el emisor. Conserve
los libros SQLite y los GID para no perder deduplicación ni trazabilidad.
Las sesiones MCP autenticadas de Metis (`metis` y `metis-chatgpt`) ofrecen
`metis_asana_create_task` cuando
están configuradas la política y la conexión saliente. Esa herramienta usa
el mismo libro durable y la evaluación del Gateway; no requiere instalar
un emisor adicional en la cuenta de Metis. Las creaciones de Asana cuyo
nombre y contexto figuran en `routing` se rechazan en el plan de escrituras
productivo anterior, para evitar un segundo POST por otra ruta. La activación
de la política y la conexión saliente requieren configuración externa.
Meli y Val necesitan sus propios consentimientos, identidades,
proyectos, audiencias, fuentes y límites aprobados. No se accede a sus
cuentas ni memorias.

Si la renovación del OAuth saliente de Asana devuelve una respuesta inválida
o incierta, el Gateway deja una barrera durable `UNCERTAIN` antes de intentar
otra operación. No se vuelve a usar el refresh token anterior: la persona
titular debe reconectar el mismo grant mediante
`/asana-dispatch/oauth/setup`, con la clave ya guardada fuera del repositorio.
No se copia la clave, el token ni la respuesta del proveedor a chats o logs.
El nuevo grant reemplaza el par cifrado y limpia la barrera. En una migración
desde versiones anteriores, cualquier token ya próximo a vencer también
queda bloqueado hasta reconexión, pues un intento previo pudo haberlo rotado.

El actuador productivo actual de Metis (`metis-gateway-bridge/WriteBridge.gs`
y `metis-orchestrator-prototype/src/ProductiveAdapter.gs`) sigue disponible
para tareas no incluidas en `routing`. Para las tareas elegibles, Metis debe
llamar a `metis_asana_create_task`, no al plan productivo genérico. Un evento
posterior que solo incluya `task_gid` y `created_by` no demuestra la
procedencia del Dot y se rechaza.
