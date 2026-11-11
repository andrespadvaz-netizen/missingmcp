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

## Descubrimiento global y autorizaciones por proyecto

La sesión MCP autenticada de Metis expone `metis_asana_workspace_inventory`
cuando hay OAuth saliente y `ASANA_WORKSPACE_GID`. Recorre proyectos visibles
mediante la API de Asana, sin escribir ni leer tareas. Devuelve nombre, GID y
privacidad de proyectos `public_to_workspace`; de privados no registrados
devuelve solo un conteo. Otras clases de privacidad restringida también se
cuentan sin exponer sus metadatos. Si Asana no informa la privacidad, la
consulta falla cerrada. Un privado registrado puede mostrar sus metadatos.
`authorized` indica una autorización de servidor: descubrir un proyecto jamás
lo agrega a la política. Más de diez páginas dan `complete: false`. El OAuth
solo descubre lo accesible a la cuenta conectada, no cuentas ni memorias
ajenas.
`project_owner_candidate_gid` es solo un dato de Asana para preparar la
revisión del responsable humano; nunca asigna tareas ni autoriza acciones por
sí mismo. El `owner_gid` aprobado en la política sigue mandando.
Los nombres del inventario se decodifican para mostrarlos; toda decisión de
autorización y correlación usa el GID, incluso cuando dos proyectos comparten
nombre.
El administrador puede fijar `ASANA_WORKSPACE_ADMIN_DISCOVERY=true` en el
servicio para incluir también metadatos de proyectos privados accesibles a su
OAuth. Esta opción **solo amplía el inventario de lectura**: `authorized`
permanece falso hasta que una entrada de proyecto fije contexto, audiencia,
fuentes y responsable humano. No lee tareas, cuentas ni memorias privadas de
Meli o Val ni habilita sus Dots.

Para operar varios proyectos, `ASANA_METIS_DOT_POLICY_JSON` acepta un registro
con `workspace_gid`, `creator_gid` y `projects`. Cada clave de `projects` es un
GID; cada entrada fija `context`, `owner_gid`, `privacy_setting`, `audiences`,
`sources`, `eligible_task_names`, `routing`, `specialties` y `agent_gids`.
`project_owner_gid` puede fijarse por separado. La forma anterior de un solo
proyecto sigue funcionando. Ejemplo sintético, sin datos reales:

```json
{
  "workspace_gid": "100", "creator_gid": "55",
  "projects": {
    "200": {
      "context": "SHOKKO", "owner_gid": "44",
      "privacy_setting": "private", "audiences": ["project_members"],
      "sources": ["current_request"], "eligible_task_names": [],
      "routing": {}, "specialties": [], "agent_gids": []
    }
  }
}
```

Cada entrada necesita autorización verificable del contexto, audiencia,
fuentes y responsable humano. Un privado solo admite `project_members`; un
proyecto visible al workspace solo admite `workspace`. Antes del POST el
Gateway compara la privacidad real en Asana. Un proyecto descubierto pero no
registrado responde `project_not_authorized`. `agent_gids: []` deja únicamente
tareas humanas. No se incorpora automáticamente .Final_Final ni se activan
Dots, cuentas o memorias de Meli y Val por pertenecer al workspace.

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

## Costo compartido y admisión de encargos

La consola real de Venture Quest informa **50 solicitudes adicionales por
ciclo, MXN 11 cada una, máximo MXN 550**, compartidas entre Dash y los
agentes. El conector disponible no permite leer ni reservar el saldo global.
La pantalla de administración «Gestionar el uso bajo demanda» muestra que
Asana tiene activo «Establece un límite de gasto» en 50 solicitudes por ciclo
y afirma que dejará de ejecutar solicitudes al llegar a ese límite. Es un
tope aplicado por Asana a la cuenta, no una lectura automática disponible
para el Gateway; puede cambiarse en la consola. Las solicitudes bajo demanda
empiezan después de agotar las incluidas y prepagadas.

El administrador aprobó conservar el límite global de Asana de 50 solicitudes
y MXN 550 por ciclo, compartido con Dash, y aceptar que Asana rechace
encargos cuando se agote. El Gateway **no conoce el saldo restante** ni puede
contar las solicitudes internas que un solo encargo de agente consumirá. Por
ello no traduce encargos a pesos ni presenta un contador local como saldo
global. Asana aplica el límite monetario; el Gateway solo admite un número
acotado de intentos de encargo durante el ciclo.

Para habilitar esa admisión, el administrador debe comprobar que el límite
en Asana sigue activo y configurar `ASANA_AI_SHARED_LIMIT_JSON` fuera del
repositorio. La declaración incluye workspace, GID del administrador,
fechas del ciclo, 50 solicitudes, MXN 11 por solicitud, MXN 550, confirmación
de aplicación por Asana y un `gateway_handoff_limit` de 1 a 50 para **todos**
los agentes del Gateway, no un presupuesto individual. Ejemplo sintético:

```json
{"workspace_gid":"100","operator_gid":"55","cycle_start":"2026-11-05","cycle_end":"2026-12-05","provider_cap_requests":50,"provider_cap_mxn":550,"price_per_request_mxn":11,"provider_cap_enforced":true,"gateway_handoff_limit":10}
```

Si falta la declaración, no corresponde al workspace o administrador, el
ciclo terminó o los importes exceden los aprobados, el despacho queda
bloqueado. La reserva SQLite de cada encargo ocurre antes del POST y es
atómica ante concurrencia; una respuesta incierta conserva el cupo y exige
conciliación sin repetir el POST. Los cupos no se reembolsan automáticamente.
La declaración del administrador no sustituye una API de saldo: si Dash gasta
entre la comprobación y el despacho, Asana puede rechazarlo. Ante cambios del
límite en la consola, retire o actualice la declaración antes de continuar.
La creación de la tarea humana es independiente del despacho pagado. La
evaluación local sin escritura sigue disponible; el comando aislado
`scripts/asana_agents.py --dispatch` continúa bloqueado.

La [especificación pública de Asana](https://github.com/Asana/openapi/blob/e5c7dec74dcea63207156a7cc96d06f82075a1d3/defs/asana_sdk_oas.yaml)
documenta `GET /workspaces/{workspace_gid}/ai_studio/runs`, restringido a
cuentas de servicio de organizaciones con AI Studio. Devuelve créditos de
ejecuciones de reglas de AI Studio. No demuestra el saldo de solicitudes
adicionales de AI Teammates compartido con Dash; no se usa para levantar el
bloqueo.

Una API oficial de saldo o un contador compartido con Dash permitirían decidir
antes de cada encargo si queda cuota. El mecanismo actual acepta explícitamente
la posibilidad de rechazo por Asana y depende de que su límite global permanezca
activo. No se ha realizado ningún despacho pagado de prueba.

## Propuesta y revisión

Si ya existe un encargo autorizado, `task.proposal` registra el GID de un
comentario no vacío de su agente en Asana como propuesta pendiente. Luego
una persona con `owner_gid` deja un comentario exactamente `METIS_APPROVE`
o `METIS_REJECT` después de la propuesta; `task.review` verifica su autor,
fecha y secuencia y registra el comentario,
la decisión y el revisor. La propuesta no aprueba ni cierra la tarea humana.

Para revertir la entrada, retire la política o desconecte el emisor. Conserve
los libros SQLite y los GID para no perder deduplicación ni trazabilidad.
Para retirar un solo proyecto, elimine su entrada. Sus eventos pendientes
quedarán bloqueados hasta conciliación explícita; no cambie GID ni reutilice
claves. El inventario de lectura puede seguir disponible sin política de
escritura.
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
Una respuesta de renovación válida puede omitir un nuevo `refresh_token`; en
ese caso se conserva el anterior, conforme a OAuth 2.0. Si trae un reemplazo
inválido o faltan el access token o la duración, permanece la barrera.
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
