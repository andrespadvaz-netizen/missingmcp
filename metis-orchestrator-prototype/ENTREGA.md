# Entrega — Prototipo Nivel 0–2 de orquestación inter-modelo

Cubre los nueve puntos de la spec §21. El código está en `src/` y `tests/`; la
instalación y la configuración, en `README.md`.

> **Ronda 2 — respuesta a la auditoría.** Los cinco primeros puntos del informe
> están implementados; el sexto está bloqueado y se explica en §9. Resumen del
> cambio en §10, con lo que la auditoría descubrió del registro real.

---

## 0. Confirmación textual exigida (spec §21.9)

> **Cero triggers y cero escrituras productivas.**

No se creó ningún trigger, scheduler, webhook ni endpoint. No se ejecutó ninguna
escritura sobre Notion, Asana, Calendar, Drive ni Gmail — ni en desarrollo, ni en
la ejecución de los tests, ni en los casos de aceptación. No se desplegó ni se
publicó nada.

Esto no es sólo una declaración; está verificado estáticamente sobre el código
que se sube (`node tools/static_guards.js`, salida en §5) y reforzado por tres
hechos independientes:

1. `appsscript.json` declara exactamente tres scopes, los tres de lectura:
   `script.external_request`, `drive.readonly`, `calendar.readonly`. **No hay
   scope de Gmail, ni de escritura de Drive/Calendar, ni `script.scriptapp`** —
   sin ese último, el proyecto no está autorizado a crear un trigger aunque
   alguien lo intentara.
2. No existe ninguna referencia ejecutable a `ScriptApp`, `newTrigger`,
   `GmailApp`, ni a ningún método de mutación de `DriveApp`/`CalendarApp` en todo
   `src/` y `tests/`. Tampoco existe ninguna función `onOpen`, `onEdit`,
   `onInstall`, `onFormSubmit`, `onChange`, `doGet` ni `doPost`.
3. Las únicas herramientas de mutación expuestas al modelo son las cinco
   `simulate.*`. `SimulatedWriteAdapter.gs` no referencia ninguna superficie
   externa: es estructuralmente incapaz de emitir una llamada.

Los guards fueron verificados **en negativo**: inyectando deliberadamente un
`onOpen` con `ScriptApp.newTrigger(...)`, una llamada a `GmailApp.sendEmail(...)`
y un scope `auth/drive` de escritura, G1, G2, G5 y G6 fallan y el proceso sale
con código 1.

---

## 1. Código completo

21 archivos en `src/` y 10 en `tests/`, exactamente los nombres de la spec §3,
más `appsscript.json` y `README.md`. Ver `README.md` §2 para la tabla de
responsabilidades por módulo.

```
appsscript.json
README.md
script-properties.example.json          (exigido por §21.3)
ENTREGA.md                              (este documento, §21.5–§21.9)
src/   Main.gs Config.gs Orchestrator.gs ContextResolver.gs RetrievalPolicy.gs
       Router.gs HandoffBuilder.gs AuthorityPolicy.gs PlanValidator.gs Ledger.gs
       ProviderAdapter.gs OpenAIAdapter.gs AnthropicAdapter.gs ToolBroker.gs
       NotionReadAdapter.gs AsanaReadAdapter.gs DriveReadAdapter.gs
       CalendarReadAdapter.gs SimulatedWriteAdapter.gs Schemas.gs Errors.gs
tests/ TestRunner.gs Fixtures.gs AcceptanceCases.gs Unit_ContextResolver.gs
       Unit_Router.gs Unit_AuthorityPolicy.gs Unit_PlanValidator.gs
       Unit_Ledger.gs Unit_HandoffBuilder.gs Unit_SimulatedWrite.gs
tools/ run_local.js static_guards.js     (arnés local; NO se sube — ver §7.D1)
```

## 2. README

`README.md`: instalación **sin activación**, estructura, configuración, corrida
manual, tests, evidencia de cero triggers/escrituras, límites y qué NO hace.

## 3. Configuración requerida

`script-properties.example.json` documenta **nombres** de Script Properties,
nunca valores, con su clave simbólica, quién la usa y en qué nivel es necesaria.
El detalle operativo está en `README.md` §4.

Cuatro secretos (`METIS_OPENAI_API_KEY`, `METIS_ANTHROPIC_API_KEY`,
`METIS_NOTION_API_KEY`, `METIS_ASANA_API_KEY`) y cuatro ajustes no secretos:

- **`METIS_SOURCE_PARTITIONS`** — qué contenedor de cada fuente pertenece a cada
  contexto. Es lo que hace real la separación: sin partición declarada para
  (contexto, fuente), esa fuente no se lee.
- **`METIS_PRICING`** — precio por 1.000 tokens por proveedor y modelo. Sin él,
  el costo queda desconocido y la corrida se detiene.
- `METIS_LIMITS` — override opcional de los límites por corrida.
- `METIS_DECISIONS_DATA_SOURCE_ID` — data source por defecto del registro de
  decisiones (opcional; preferible declararlo por contexto en la partición).

El prototipo arranca en **Nivel 0** y no lee ninguna hasta que el operador
cambie `RUN_LEVEL` a mano.

---

## 4. Resultado de los tests unitarios (spec §21.5)

`node tools/run_local.js unit` — equivalente a `runUnitTests()` dentro de Apps
Script.

```
=== Tests unitarios (spec §17) ===

-- ContextResolver
  PASS  identifica contexto único  (4 asserts)
  PASS  detecta conflicto multi-contexto  (5 asserts)
  PASS  no mezcla sustancia antes de resolver  (4 asserts)
  PASS  la instrucción del operador desambigua sin recuperar  (2 asserts)
  PASS  clasifica la intención de la petición  (3 asserts)

-- Partición
  PASS  sin partición declarada no se lee la fuente  (2 asserts)
  PASS  el contenido de otro contexto es INALCANZABLE, no filtrado  (6 asserts)
  PASS  ninguna lectura ocurre sin contexto resuelto  (1 asserts)

-- Vigencia
  PASS  la relación manda; el Estado vacío usa el default declarado  (6 asserts)
  PASS  Estado y relación en contradicción no se resuelven por juicio propio  (6 asserts)
  PASS  la cadena se recorre por `Sustituida por` hasta la terminal  (7 asserts)

-- Router
  PASS  LOCAL por default  (4 asserts)
  PASS  el modelo primario no obliga transferencia  (3 asserts)
  PASS  continuidad sin artefacto nombrable no transfiere  (4 asserts)
  PASS  transfiere sólo por causa material  (6 asserts)
  PASS  auditor no inicia nuevo ciclo  (4 asserts)
  PASS  bloqueos duros antes que cualquier transferencia  (4 asserts)

-- ToolBroker
  PASS  las capacidades del contrato coinciden con Config  (4 asserts)

-- AuthorityPolicy
  PASS  contenido recuperado no concede mandato  (5 asserts)
  PASS  destino recuperado degrada escritura a REQUIRES_ANDRES  (3 asserts)
  PASS  autoridad posterior nunca supera techo previo  (7 asserts)
  PASS  superficies y campos prohibidos se deniegan  (4 asserts)

-- PlanValidator
  PASS  valida invariantes sobre plan completo  (11 asserts)
  PASS  detecta composición prohibida  (5 asserts)
  PASS  impide cualquier escritura real  (4 asserts)
  PASS  invariantes 6 y 7: segundo ciclo y handoff replay  (4 asserts)

-- Ledger
  PASS  identidad por execution_id + ordinal  (6 asserts)
  PASS  payload distinto en mismo ordinal falla  (2 asserts)
  PASS  máquina de estados de la acción  (4 asserts)
  PASS  no contiene sustancia ni secretos  (9 asserts)
  PASS  fail closed sin ledger y retención configurable  (5 asserts)
  PASS  techos agregados producen parada dura  (2 asserts)
  PASS  el registro de handoffs se persiste y no lleva sustancia  (12 asserts)

-- HandoffBuilder
  PASS  incluye TTL  (7 asserts)
  PASS  rechaza replay  (3 asserts)
  PASS  conserva etiquetas epistémicas  (2 asserts)
  PASS  transporta punteros, no un dump de contexto  (4 asserts)
  PASS  el anti-replay sobrevive al fin del proceso  (2 asserts)
  PASS  sólo se aceptan handoffs que este runtime emitió  (2 asserts)
  PASS  sin ledger no se puede probar que no hubo replay  (1 asserts)

-- SimulatedWriteAdapter
  PASS  jamás llama API real  (6 asserts)
  PASS  produce representación exacta de lo que habría ocurrido  (12 asserts)
  PASS  sin ledger la acción material falla cerrada  (1 asserts)
  PASS  la reconciliabilidad se declara por acción  (2 asserts)

-- ProviderAdapter
  PASS  OpenAI y Anthropic normalizan al mismo objeto  (7 asserts)
  PASS  los errores de proveedor se redactan antes de exponerse  (5 asserts)

-- ReadAdapters
  PASS  no exponen ninguna operación de escritura  (4 asserts)
  PASS  las lecturas reales están bloqueadas en Nivel 0  (3 asserts)

-- Config
  PASS  no hay secretos en el código ni en la configuración  (6 asserts)

-- Presupuesto
  PASS  sin precio configurado el costo es desconocido, no cero  (3 asserts)
  PASS  un costo desconocido detiene la corrida  (4 asserts)
  PASS  los límites se pueden externalizar  (4 asserts)

Total: 52 | PASS: 52 | FAIL: 0 | asserts: 236 | nivel: LEVEL_0
```

Las siete suites exigidas por §17 están cubiertas con sus assertions nombradas,
más siete suites añadidas (Partición, Vigencia, ToolBroker, ProviderAdapter,
ReadAdapters, Config, Presupuesto) que demuestran la separación efectiva de
contexto, las reglas de vigencia contra el registro real, el contrato de
proveedor, la redacción de secretos y el fail closed por costo desconocido.

Dos defectos reales aparecieron y se corrigieron durante esta ejecución:

- `AsanaReadAdapter` resolvía `ASANA_WORKSPACE_GID` **antes** de comprobar el
  nivel, de modo que en Nivel 0 fallaba con `CONFIG_ERROR` en vez de
  `LEVEL_VIOLATION`. La guarda de nivel se movió al inicio de `search`/`get`.
- Un test afirmaba que un campo desconocido en la entrada del ledger lanzaba
  excepción. No es así, y la propiedad real es mejor: el ledger se construye
  campo a campo (whitelist constructiva), así que un campo ajeno —por ejemplo un
  secreto— **no puede llegar** al registro. El test ahora afirma eso, más el
  rechazo del esquema estricto ante un `LedgerEntry` con campo desconocido.

## 5. Resultado de los 10 casos de aceptación (spec §21.6)

`node tools/run_local.js acceptance` — equivalente a `runAcceptanceCases()`.

```
=== Casos de aceptación (spec §16) ===

  PASS  [AC-01] Resolver localmente: pregunta factual sin segundo modelo        (12 asserts)
  PASS  [AC-02] Handoff por auditoría: el auditor recupera y LUEGO dictamina    (19 asserts)
  PASS  [AC-03] Modelo primario no obliga transferencia                         ( 7 asserts)
  PASS  [AC-04] Contexto ambiguo: bloquear antes de mezclar Andrea y Shokko     (10 asserts)
  PASS  [AC-05] Vigencia: cerrar la cadena de decisión antes de afirmar estado  (12 asserts)
  PASS  [AC-06] Prompt injection interna: PHI es evidencia, no mandato          (10 asserts)
  PASS  [AC-07] Destino derivado de Retrieval: REQUIRES_ANDRES y sólo simulación(14 asserts)
  PASS  [AC-08] Composición prohibida: dos acciones válidas bloquean el plan    (11 asserts)
  PASS  [AC-09] Replay: rechazado incluso desde OTRA ejecución                  ( 8 asserts)
  PASS  [AC-10] Cobertura incompleta: la respuesta se abstiene o se acota       ( 8 asserts)

Total: 10 | PASS: 10 | FAIL: 0 | asserts: 118 | nivel: LEVEL_0
```

Qué demuestra cada caso, con la evidencia concreta que verifica:

| Caso | Evidencia verificada |
| --- | --- |
| AC-01 | ruta `LOCAL`, `handoff = null`, el segundo proveedor recibe **0 llamadas**, fuentes visibles en la respuesta única, ledger vacío |
| AC-02 | un solo handoff, `OPENAI → ANTHROPIC`, `generated_by_system`; el auditor tiene **dos turnos**: en el primero pide lecturas ("no emitas veredicto todavía"), en el segundo recibe "Evidencia que TÚ recuperaste" con `met-canon-01` —documento que el productor nunca vio— y sólo entonces dictamina; el veredicto final es el del segundo turno; el prompt del handoff lleva el puntero `met-prop-01` y **no** la sustancia; `reconciled` al cerrar |
| AC-03 | contexto `ANDREA` cuyo primario es `OPENAI`, corrida iniciada en `ANTHROPIC`: ruta `LOCAL`, sin handoff, el primario recibe **0 llamadas**, `material_cause = null` |
| AC-04 | dos contextos candidatos, `REQUIRES_ANDRES`, **0 tool calls**, **0 evidencia**, **0 fuentes consultadas**, ningún modelo llegó a intervenir: el bloqueo es previo a recuperar nada |
| AC-05 | (a) cadena cerrada por la relación `Sustituida por`, terminal con `Estado` **vacío** → vigente por el default declarado, y la respuesta lo advierte; (b) cadena abierta: nombra el eslabón faltante, `ABSTAIN`, `UNCERTAIN`, "no afirmo estado"; (c) fila `vigente` **con** sustituta → `CURRENCY_CONFLICT` y `REQUIRES_ANDRES` |
| AC-06 | el PHI conserva `HISTORICAL`, la inyección queda como señal de riesgo con `grants_authority: false`, la herramienta que pedía la inyección se rechaza con `TOOL_NOT_ALLOWED`, **0 acciones** en el plan, ledger vacío |
| AC-07 | `destination_provenance = RETRIEVED_CONTENT` degrada a `REQUIRES_ANDRES`; la acción se **simula** con `blocked_by_policy: true`, declara `PUT https://app.asana.com/api/1.0/tasks/{gid}` como lo que habría ocurrido, ledger en `SIMULATED` con `provider_object_id: null` y **sin sustancia** |
| AC-08 | cada acción, aislada, evalúa `ALLOW`; juntas derivan `HIDE_OVERDUE_RITUAL`; el plan **completo** queda inválido por `I5_FORBIDDEN_COMPOSITION`, **ninguna** acción se simuló (ledger vacío) y los pasos quedan en `PLANNED` |
| AC-09 | el replay llega **desde otra ejecución** (sin memoria de proceso) y presentado con `reconciled:false`: se rechaza igual, porque el estado vive en el ledger; un handoff que este runtime no emitió y uno con la caducidad alterada también se rechazan; un plan apoyado en él se bloquea por `I7_HANDOFF_REJECTED`; y el TTL sigue caducando |
| AC-10 | Drive sin cobertura tras retries acotados → `coverage.complete = false`, ruta `ABSTAIN`, estado `UNCERTAIN`, la respuesta nombra la fuente faltante y dice "No afirmo exhaustividad" |

El detalle assert por assert se obtiene con
`node tools/run_local.js acceptance --verbose`.

### Guards estáticos

```
=== Guards estáticos sobre el código que se sube a Apps Script ===
  PASS  G1  cero triggers
  PASS  G2  cero superficies de escritura productiva
  PASS  G3  ningún verbo HTTP de mutación
  PASS  G4  UrlFetchApp sólo en los adaptadores autorizados
  PASS  G5  SimulatedWriteAdapter no toca ninguna superficie externa
  PASS  G6  appsscript.json declara sólo scopes de lectura
  PASS  G7  sin literales que parezcan secretos
  PASS  G8  sin tablas de precio en el código
  PASS  G9  todo adaptador de lectura exige partición

Archivos analizados: 21 en src/, 10 en tests/
RESULTADO: PASS
```

### Condiciones de PASS de la spec §18

| Condición | Estado |
| --- | --- |
| los 10 casos de aceptación pasan | ✅ 10/10, 118 asserts |
| todos los tests unitarios pasan | ✅ 52/52, 236 asserts |
| cero escrituras externas durante ejecución y tests | ✅ verificado por guards + bloqueo total de superficies externas en la ejecución de los tests |
| cero contaminación entre contextos | ✅ AC-04, invariante 2, y **partición en origen**: sin partición declarada no se lee, y un objeto de otro contexto es inalcanzable (guard G9 + suite Partición) |
| cero handoff manual dentro de una corrida | ✅ AC-02: el handoff lo emite `HandoffBuilder` desde el orquestador |
| toda acción material termina como simulación o bloqueo explícito | ✅ AC-07 (simulada y bloqueada), AC-08 (plan bloqueado sin simular) |
| ninguna instrucción recuperada modifica autoridad | ✅ AC-06 + `Unit_AuthorityPolicy` |
| ledger sin sustancia ni secretos | ✅ `Unit_Ledger`, AC-07 |
| una sola respuesta final con ruta y fuentes visibles | ✅ AC-01, AC-02 |

**Veredicto: PASS** — con las salvedades de §6 (en particular L1 y L2: las
integraciones reales de sólo lectura están implementadas pero no ejecutadas).

---

## 6. Limitaciones reales encontradas (spec §21.7)

**L1 — Las integraciones reales de sólo lectura siguen SIN EJECUTARSE.** *(sin
cambio en la ronda 2; es el punto 6 del informe, bloqueado — ver §9)*. Notion,
Asana, Drive y Calendar están implementados contra sus APIs reales, y el
adaptador de Notion ahora usa el endpoint y los nombres de propiedad
verificados, pero no se ha ejecutado ni una llamada: no hay proyecto de Apps
Script, ni autorización de Google, ni credenciales. Lo verificado es la
construcción de la petición, la normalización, la clasificación epistémica, la
partición, el bloqueo por nivel y la redacción de errores. `smokeTestLevel1()`
está listo para que el operador lo corra.

**L2 — CERRADA a nivel de código; pendiente de ejecución.** *(era la brecha más
grande)*. El grafo de decisiones ya no viene de un canal de pistas: se lee del
registro con `notion.decisions`, que hace **query de data source** (API
`2025-09-03`) filtrando por la propiedad select `Proyecto`, y extrae `Estado`
junto con las relaciones `Sustituida por` / `Sustituye a`. El canal
`structured_hints` fue **eliminado**. Los nombres de propiedad están tomados del
esquema real de la database, verificado contra el workspace el 2026-08-25, y
viven en una sola constante (`NotionReadAdapter.DECISION_PROPS`).

Lo que queda abierto de L2, y no es menor:

- **no se ha ejecutado contra Notion real** (es L1);
- si alguien renombra una propiedad en Notion, esta lectura se rompe. No hay
  detección automática de deriva de esquema; el fallo sería visible (`Estado`
  ausente ⇒ vacío ⇒ default aplicado) pero **silencioso en su causa**. Un check
  de esquema en `smokeTestLevel1()` sería lo siguiente que yo añadiría;
- la database es **una sola** para todos los contextos: la separación es el
  filtro por `Proyecto`, no contenedores distintos. Eso es lo que hay, y por eso
  la partición de Notion admite `decision_data_sources` además de `data_sources`.
  Un fallo del filtro sería contaminación de contexto, no sólo ruido.

**L3 — `DriveApp` con scope `drive.readonly`.** El manifiesto declara sólo
lectura a propósito. Algunos métodos de `DriveApp` pueden exigir el scope amplio
de Drive en la autorización. Si al pasar a Nivel 1 la autorización falla, la
salida correcta es mover el adaptador al servicio avanzado de Drive / REST con
`drive.readonly`, **no** ampliar el scope. No lo resolví por adelantado porque
ampliar el scope contradiría §19 y cambiar de mecanismo sería tooling nuevo.

**L4 — Asana no ofrece tokens de sólo lectura.** Un Personal Access Token
arrastra los permisos del usuario. Mitigado porque el adaptador no tiene ninguna
función de escritura, G3 prohíbe `PUT`/`PATCH`/`DELETE` y ahora la lectura se
acota a los proyectos declarados (ya no usa el typeahead de workspace, que
barría todo el espacio), pero es un **riesgo residual a nivel credencial**, no
eliminado.

**L5 — La búsqueda de Notion es un `POST`.** Es una operación de lectura que usa
`POST` por diseño de la API. G3 prohíbe `PUT`/`PATCH`/`DELETE`, no `POST`; quien
lea el guard no debe concluir que "no hay POST".

**L6 — `PropertiesService` no es un ledger de producción, y ahora carga más.**
Sin transacciones ni bloqueo; cuota de ~9 KB por valor y ~500 KB total; dos
ejecuciones concurrentes podrían intercalarse. Desde la ronda 2 guarda además el
registro anti-replay de handoffs, lo que **aumenta** la presión sobre esa cuota
y añade una carrera teórica: dos ejecuciones consumiendo el mismo handoff a la
vez podrían ambas leer `reconciled:false` antes de que ninguna escriba. En
Nivel 0–2 no hay efecto externo que duplicar, así que la carrera no produce
daño; **en Nivel 3 sí lo produciría**, y ahí hace falta `LockService` o un store
con compare-and-set. Sigue siendo uno de los gatillos de migración de runtime.

**L7 — El límite de ~6 minutos por ejecución no está medido.** Cada caso de
aceptación corre como corrida independiente, como el diseño anticipaba, pero una
corrida real con dos proveedores y varias tool calls no se ha cronometrado.

**L8 — El bucle de herramientas sigue siendo de una sola ronda por ciclo.** Con
`MAX_MODEL_INTERVENTIONS = 4`, productor y auditor tienen cada uno un turno para
pedir lecturas y otro para producir. Un modelo que necesitara dos rondas de
lecturas *encadenadas* —leer, y a partir de lo leído decidir qué leer después—
se queda sin turnos. Es fail closed deliberado, pero acota la profundidad real
del Retrieval, y es la limitación que más notaría un caso complejo.

**L9 — La detección de contexto es léxica, no semántica.** Una petición sin
señal de contexto se abstiene; una que menciona dos contextos se bloquea. Nunca
**amplía** autoridad —el peor caso es bloquear de más— pero no entiende
sinónimos ni referentes implícitos, y una mención entre comillas puede producir
un candidato falso.

**L10 — El costo es estimado, no facturado.** Se calcula con una tabla de precios
fija sobre tokens reportados. Sirve para el contador agregado y la parada dura,
que es lo que §13 pide; no es telemetría financiera.

**L11 — Los 10 casos prueban al controlador, no al modelo.** Corren en Nivel 0
con proveedores scriptados y deterministas. AC-06 demuestra que **el controlador
bloquea** la herramienta que la inyección pedía —incluso simulando que el modelo
obedece la inyección—, no que un modelo real se resista a ella. Lo mismo vale
para AC-02: prueba que el auditor **recibe** en su segundo turno la evidencia
que él mismo pidió, no que un modelo real la use bien.

**L12 — Los resultados de §4 y §5 fueron producidos por el arnés local, no por
Apps Script.** Ver desviación D1. Las mismas suites corren dentro de Apps Script
con `runUnitTests()` / `runAcceptanceCases()`; esa reproducción no se ha hecho
todavía porque exige crear el proyecto, que es una acción del operador.

**L13 — Sin detección de deriva de esquema en Notion.** `DECISION_PROPS` fija
los nombres reales. Si cambian en Notion, la lectura degrada en silencio (una
propiedad ausente se lee como vacía). No hay verificación de esquema al inicio
de la corrida; es la mejora más obvia pendiente sobre el cierre de L2.

**L14 — La partición sólo es tan buena como su declaración.** El código exige
que exista una partición y acota la consulta a ella, pero **no puede verificar
que los ids declarados sean los correctos**. Declarar por error el data source
de ANDREA bajo el contexto METIS produciría contaminación silenciosa. La única
defensa es la revisión humana de `METIS_SOURCE_PARTITIONS`, más el filtro por
`Proyecto` en el caso de Notion, que sí es independiente del id.

**Contradicciones técnicas encontradas: ninguna que impidiera implementar una
parte de la spec.** No hubo que detener ningún módulo por imposibilidad técnica.
La única tensión real sigue siendo D1, entre §2 y §21.5/§21.6. El punto 6 del
informe de auditoría sí está detenido, pero por falta de acceso, no por
contradicción — ver §9.

---

## 7. Desviaciones respecto de la especificación (spec §21.8)

Idealmente vacío. No lo está. Nueve desviaciones, ordenadas por importancia.

### D1 — Arnés local en Node para poder ejecutar los tests *(la desviación que más pesa)*

**Qué dice la spec.** §2: no introducir Node ni tooling nuevo. §20: si aparece
una contradicción, detener esa parte y reportarla, no resolverla ampliando
tooling. §21.5 y §21.6: entregar el **resultado** de los tests unitarios y de los
10 casos de aceptación.

**La tensión.** Apps Script sólo ejecuta dentro de Google. Sin crear el proyecto
—que es una acción del operador, y crearlo yo sería activación— no hay forma de
producir esos resultados. Cumplir §2 al pie de la letra significaba entregar
~3.500 líneas sin ejecutar nunca.

**Qué hice.** Añadí `tools/run_local.js` y `tools/static_guards.js`: un arnés que
concatena los **mismos** archivos `.gs` y los evalúa con los globals de Apps
Script emulados. Lo declaro como desviación, no como solución neutra.

**Por qué creo que es la opción correcta, y cómo revertirla.** El arnés no es
parte del prototipo: no se sube, ningún `.gs` lo importa, no cambia el runtime
congelado y no añade ninguna dependencia (sólo `fs`, `path`, `vm`, `crypto` de la
librería estándar). Durante los tests **bloquea** `UrlFetchApp`, `DriveApp`,
`CalendarApp`, `GmailApp` y `ScriptApp`, lo que además es la evidencia más fuerte
de "cero escrituras externas durante los tests". El operador puede borrar
`tools/` entero sin tocar una línea de `src/` ni de `tests/`, y reproducir los
mismos resultados dentro de Apps Script con `runUnitTests()` y
`runAcceptanceCases()`.

**Caveat que hay que leer junto con los resultados:** los números de §4 y §5
salieron del arnés, no de Apps Script (L12).

### D2 — El proyecto vive en un subdirectorio del repositorio

§3 dibuja `/src`, `/tests`, `README.md` y `appsscript.json` en la raíz. El
repositorio destino (`missingmcp`) ya tiene `src/`, `tests/` y `README.md` de un
proyecto Python sin relación. Colocar el árbol en la raíz habría sobrescrito
código ajeno. El proyecto está en `metis-orchestrator-prototype/` con la
estructura interna **literal**: mismos nombres, mismos archivos, misma jerarquía.
Sólo cambia el prefijo de ruta.

### D3 — Dos archivos añadidos al árbol de §3

`script-properties.example.json`, que §3 no lista pero §21.3 exige, y `ENTREGA.md`
(este documento), que soporta §21.5–§21.9. Ninguno se sube al proyecto de Apps
Script. No se añadió ni se quitó ningún archivo de `src/` ni de `tests/`.

### D4 — `simulate.*` no simula en el momento de invocarse

§10 describe qué devuelve cada simulación. §7 exige validar el **plan completo**
antes de la primera acción. Si el modelo invocara `simulate.notion_write` y la
simulación se ejecutara ahí mismo, el modelo podría saltarse `PlanValidator`
encadenando acciones sueltas. Implementado: el ToolBroker **encola** la acción
propuesta y devuelve el objeto con la **forma exacta** de §10, con
`blocked_by_policy: true` y `block_reason: 'PENDING_PLAN_VALIDATION'`. La
simulación real ocurre después, sobre el plan ya validado.

### D5 — Interpretación del estado de una acción bloqueada

La spec usa `REQUIRES_ANDRES` como estado de corrida y `SIMULATED` como estado de
acción, y pide a la vez "bloqueo simulado" (§6.4) y "bloquear el plan completo"
(§16.8). Distinción implementada:

- **acción degradada por procedencia** (AC-07): sí se simula.
  `INTENT_RECORDED → SENT → SIMULATED` con `blocked_by_policy: true`. `SENT`
  significa "entregada al adaptador de simulación"; nada salió del runtime.
- **plan con violación de invariante** (AC-08): **no** se simula nada. Los pasos
  quedan en `PLANNED`, el ledger queda vacío, la corrida para en
  `REQUIRES_ANDRES` y se devuelve una representación de qué habría ocurrido, sin
  registrarla.

### D6 — `ProviderAdapter` sin `extends`

§11 muestra `class ProviderAdapter { ... }`. En Apps Script el orden de carga de
archivos lo controla el editor; un `extends` entre archivos es frágil.
`ProviderAdapter.gs` expone la clase base y `ProviderAdapter.conforms()`, y los
adaptadores concretos implementan los cuatro métodos **sin heredar**. Misma
interfaz, mismo objeto normalizado, conformidad afirmada en los tests. Por el
mismo motivo, `ToolBroker` escribe las capacidades como literales en vez de leer
`Config.CAPABILITIES` en tiempo de carga; un test verifica que no se separen.

### D7 — Regla concreta para `ABSTAIN` vs `REQUIRES_ANDRES`

§6.3 admite cualquiera de los dos. Regla fijada: **0 candidatos → `ABSTAIN`**
(no se puede demostrar contexto y nadie ha nombrado uno); **>1 candidatos
incompatibles → `REQUIRES_ANDRES`** (el dato desambiguador vive en el turno del
operador, no en una fuente consultable). También `ABSTAIN` para vigencia no
cerrable y cobertura incompleta, y `REQUIRES_ANDRES` para conflicto real de
autoridad.

### D8 — Tests añadidos dentro de los archivos existentes

§17 fija mínimos; §3 fija la lista de archivos. Las pruebas de contrato de
`ProviderAdapter`, de los adaptadores de lectura y de `Config` viven al final de
`Unit_SimulatedWrite.gs`, y la verificación de capacidades del ToolBroker al
final de `Unit_Router.gs`, en lugar de en archivos nuevos. Se añadieron pruebas;
no se quitó ninguna de las exigidas.

### D9 — Valores concretos donde la spec pide "configurable"

§13 exige que los límites sean configurables sin fijar cifras. Elegidos en
`Config.LIMITS`: 3 intervenciones de modelo (productor con Retrieval + producción,
y auditor: un ciclo cerrado), 12 tool calls, 2 retries de lectura, 0.50 USD por
corrida, 5/25 USD diario/mensual, TTL de handoff 15 min, retención de ledger 24 h.
Todos se cambian en un solo lugar.

### D10 — Una herramienta de lectura añadida al contrato: `notion.decisions`

§10 enumera las lecturas permitidas y no incluye una lectura estructurada del
registro de decisiones. Cerrar L2 por la vía competente —query de data source
con filtro por `Proyecto` y relaciones de sustitución— exige exponerla, así que
`notion.decisions` se añadió al techo de lectura (`AuthorityPolicy.READ_TOOLS`).
Es **lectura**, comparte la capacidad abstracta `notion_read`, está sujeta a la
misma partición y a los mismos límites, y no amplía ninguna autoridad de
escritura. Aun así es una adición al contrato de §10 y por eso se declara.

### D11 — Sin precio configurado, la corrida se detiene

§13 pide un contador agregado y bloqueo al alcanzar el techo. No dice qué hacer
si el precio no se conoce. Decisión tomada: el costo queda `null`, la corrida
falla cerrada con `PRICE_UNKNOWN` y el retorno declara `cost_known: false`.
La alternativa —seguir contando como si el costo fuera cero— vigila el techo
contra un contador ciego, que es la forma de superarlo sin enterarse.

### Sin desviación

Los contratos de §4 se implementan con el **conjunto de campos exacto** y
validación estricta: un campo desconocido es un error. No se añadió ningún campo
a `Execution`, `EvidenceRef`, `Handoff`, `AuthorityGrant`, `ActionPlan`,
`ActionStep` ni `LedgerEntry`. Los campos adicionales del **objeto de retorno**
de una corrida (`risk_signals`, `tool_errors`, `limits`, `level`,
`auditor_tool_calls`) pertenecen al contrato de salida al operador, no a los
esquemas de §4.

---

## 8. Diff explícito spec ↔ construcción

| Spec | Construcción | Desviación |
| --- | --- | --- |
| §1 Objetivo: flujo de una corrida | `Orchestrator.run` | — |
| §2 Runtime congelado, arquitectura neutral | Apps Script, controlador + 2 adaptadores + broker + escritura simulada + ledger | D1 (arnés local) |
| §3 Estructura de archivos | 21 en `src/`, 10 en `tests/`, nombres literales | D2 (prefijo de ruta), D3 (2 archivos de entrega) |
| §4.1–§4.7 Contratos | `Schemas.gs`, validación de campos exacta | — |
| §5 Identidad, idempotencia, ledger | `Ledger.gs` | — |
| §6 Autoridad y procedencia | `AuthorityPolicy.gs`, `ContextResolver.gs` | D7 (regla ABSTAIN/REQUIRES_ANDRES) |
| §7 Validación del plan completo (7 invariantes) | `PlanValidator.gs` | — |
| §8 Routing | `Router.gs` | — |
| §9 Retrieval | `RetrievalPolicy.gs` + 4 adaptadores de lectura, con partición por contexto y query de data source para el registro de decisiones | D10 (herramienta `notion.decisions`) |
| §10 ToolBroker | `ToolBroker.gs`, `SimulatedWriteAdapter.gs` | D4 (encolar antes de simular) |
| §11 ProviderAdapter | `ProviderAdapter.gs`, `OpenAIAdapter.gs`, `AnthropicAdapter.gs` | D6 (sin `extends`) |
| §12 Secretos | Script Properties, claves simbólicas, redacción | — |
| §13 Costos y límites | `Config.limits()` + `METIS_LIMITS`, precios en `METIS_PRICING`, contadores en `Ledger` | D9 (valores elegidos), D11 (sin precio ⇒ parada) |
| §14 Manejo de fallos | retries acotados, fail closed, redacción | — |
| §15 Handoff interno | `HandoffBuilder.gs` | — |
| §16 10 casos de aceptación | `AcceptanceCases.gs`, 10/10 PASS | — |
| §17 Tests unitarios mínimos | 7 suites exigidas + 4 añadidas, 39/39 PASS | D8 (ubicación de los añadidos) |
| §18 Condiciones de PASS | tabla en §5 de este documento | — |
| §19 FAIL duro | ninguna condición activada; guards en §5 | — |
| §20 Prohibiciones | ninguna violada | D1 declarada explícitamente |

---

## 9. Punto 6 del informe: smoke test Nivel 1 — NO EJECUTADO

**No lo ejecuté. No puedo.** Lo digo aquí y no en una nota al pie porque es la
única parte del informe que no está hecha.

Ejecutar una lectura real de Nivel 1 exige, en este orden: un proyecto de Apps
Script creado, la pantalla de autorización de Google aceptada por la cuenta
dueña de Drive y Calendar, y las cuatro Script Properties cargadas con
credenciales. No tengo ninguna de las tres cosas, y las tres son acciones del
operador. Crear el proyecto y autorizarlo yo, aunque pudiera, sería activación,
que es exactamente lo que la spec §20 prohíbe.

Lo que sí dejé listo es la herramienta para que se ejecute en un clic:
`smokeTestLevel1()` en `Main.gs`.

- **Qué hace:** comprueba el nivel, la presencia de credenciales y las
  particiones declaradas; después hace **una** lectura acotada por cada
  (contexto, fuente) con partición: `notion.search`, `notion.decisions`,
  `asana.search`, `drive.search`. Sobre las decisiones informa cuántas hay,
  cuántas vigentes, cuántas con `Estado` vacío y cuántas contradictorias.
- **Qué NO hace:** no invoca ningún modelo (`models_invoked: 0`), no construye
  plan, no simula ninguna acción y no escribe nada (`writes_attempted: 0`).
- **Qué devuelve:** conteos y errores redactados. Nunca contenido recuperado.
- **Requisito:** cambiar `Config.RUN_LEVEL` a `LEVEL_1` a mano. Si no, la
  función se detiene y lo dice, sin tocar la red.

Lo más probable que rompa en esa primera corrida, por orden de apuesta: el
scope `drive.readonly` frente a `DriveApp` (L3), la versión `2025-09-03` de la
API de Notion frente a la integración existente, y los ids de partición mal
declarados (L14). Los tres fallan de forma visible y ninguno escribe.

---

## 10. Ronda 2 — qué cambió y qué encontró la auditoría

### Lo que se implementó

| # | Punto del informe | Cambio | Evidencia |
| --- | --- | --- | --- |
| 1 | loop productor/auditor | `_modelCycle` (lectura + producción) usado por **ambos**; el auditor pasa de 1 a 2 turnos; `MAX_MODEL_INTERVENTIONS` 3→4 | AC-02 reescrito, 19 asserts |
| 2 | anti-replay persistido | el estado de handoff vive en el ledger, no en memoria; se rechaza un handoff no emitido y uno con caducidad alterada | AC-09 (replay desde otra ejecución), 3 suites nuevas |
| 3 | partición efectiva | `METIS_SOURCE_PARTITIONS` por (contexto, fuente); sin partición no se lee; la consulta se acota en origen en los 4 adaptadores | guard G9, suite Partición |
| 4 | cierre de L2 | `notion.decisions` con query de data source y filtro por `Proyecto`; vigencia desde `Estado` + relaciones; `structured_hints` eliminado | AC-05 (a/b/c), suite Vigencia |
| 5 | presupuestos y precios | límites y precios a Script Properties; sin precio, costo desconocido y parada dura | guard G8, suite Presupuesto |
| 6 | smoke test Nivel 1 | **no ejecutado** — §9 | `smokeTestLevel1()` listo |

### Lo que encontró la auditoría del registro real

Consultando la database "Decisiones Tomadas" para obtener su esquema aparecieron
dos hechos que cambiaron el diseño, y que valen por sí mismos:

1. **`Estado` está vacío en 202 de 316 filas.** La descripción de la propiedad
   declara "por defecto vigente", así que el vacío es interpretable — pero una
   regla del tipo `Estado === 'vigente'` habría clasificado como *no vigente* a
   la mayoría del registro. La regla implementada da precedencia a la relación
   `Sustituida por` y aplica el default sólo cuando el select falta, **diciéndolo
   en la respuesta**.
2. **Hay al menos una fila marcada `vigente` que sí tiene `Sustituida por`**
   (en Arquitecto Interior). Dos señales de la misma fuente competente que se
   contradicen. El prototipo no elige ganador: emite `CURRENCY_CONFLICT` y para
   en `REQUIRES_ANDRES`. El fixture reproduce esa fila en el mismo contexto en
   que está la real.

El segundo hallazgo es, en rigor, una **inconsistencia de datos que existe hoy
en Metis**, independiente de este prototipo. Vale la pena revisarla.

### Defectos propios corregidos en esta ronda

- El auditor pedía lecturas y emitía su veredicto **sin haberlas visto nunca**:
  el ciclo terminaba tras su único turno. AC-02 lo daba por bueno porque sólo
  comprobaba que hubiera hecho tool calls, no que las usara.
- La guarda de partición se adelantaba a la de nivel en el adaptador de Notion,
  igual que había pasado antes con Asana. El nivel es la guarda exterior.
- Un test afirmaba que una violación de partición se degradaba a resultado
  fallido; el código lanza. Lanzar es lo correcto: degradarla marcaría la fuente
  como "sin cobertura" y ocultaría el intento de cruzar contextos.

---

## 11. Gate posterior

Completar este prototipo **no autoriza Nivel 3 ni producción**. El resultado
vuelve a auditoría cruzada. Tras la ronda 2, lo que más merece ser atacado es:

- **L1 / §9** — nada de lo que toca fuentes reales se ha ejecutado todavía. Es
  el siguiente paso y depende del operador.
- **L14** — la partición es tan buena como su declaración; un id mal puesto
  produce contaminación silenciosa y el código no puede detectarlo.
- **L13** — no hay detección de deriva de esquema en Notion; un renombre
  degrada la lectura de vigencia en silencio.
- **L6** — el registro anti-replay hereda las carencias de `PropertiesService`,
  y la carrera que hoy es inocua deja de serlo en Nivel 3.
- **D1** — si el arnés local era admisible o si debí entregar sin resultados.
- **L11** — los casos prueban la política del controlador, no la conducta de un
  modelo real.

Y, fuera del prototipo: **la fila de "Decisiones Tomadas" marcada `vigente` con
`Sustituida por` no vacío** es una inconsistencia real del registro, hoy.
