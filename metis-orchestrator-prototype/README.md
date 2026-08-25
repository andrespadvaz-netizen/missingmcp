# Prototipo Nivel 0–2 de orquestación inter-modelo (Metis)

Implementación de la especificación técnica *"Prototipo Nivel 0–2 de orquestación
inter-modelo"*. Runtime congelado: **Google Apps Script**, proyecto aislado,
**sin triggers** y **sin escrituras productivas**.

> **Estado: no activado.** Este README instala y prueba. No despliega, no crea
> triggers, no carga secretos por ti y no autoriza Nivel 3.

---

## 1. Qué hace

Una corrida demuestra, de una sola pasada:

```
necesidad natural → contexto → Retrieval real de sólo lectura → routing
→ handoff interno → segundo modelo cuando corresponda → retorno único
```

Toda acción externa material se representa como **SIMULADA** o como **bloqueo
explícito**. El controlador de Apps Script —no el modelo— decide contexto,
autoridad, routing, límites, validación de plan y simulación.

## 2. Estructura

```
appsscript.json                manifiesto: V8, sin triggers, scopes de sólo lectura
script-properties.example.json nombres de Script Properties (nunca valores)
src/                           los 21 archivos del proyecto Apps Script
tests/                         runner manual, fixtures, 7 suites unitarias, 10 casos
tools/                         arnés local de ejecución (NO se sube a Apps Script)
```

| Módulo | Responsabilidad |
| --- | --- |
| `Config.gs` | tunables, nivel de sandbox, nombres simbólicos de secretos, contextos, superficies prohibidas, límites |
| `Errors.gs` | errores tipados + **redacción** de errores de proveedor |
| `Schemas.gs` | contratos de la spec §4 con validación de campos **exacta**, hashes, reloj e ids inyectables |
| `Ledger.gs` | ledger técnico: identidad `execution_id + ordinal`, guarda de consistencia, máquina de estados, retención, contadores de costo |
| `ContextResolver.gs` | contexto e intención; bloquea antes de mezclar sustancia de contextos incompatibles |
| `RetrievalPolicy.gs` | precedencia de fuentes, vigencia por `Estado` + relaciones de sustitución, cobertura, etiquetas epistémicas, detección de prompt injection |
| `AuthorityPolicy.gs` | techo predeclarado, fase de lectura, fase de acción (sólo estrecha), procedencia de destino |
| `Router.gs` | LOCAL por default; transferencia sólo por causa material; cierre de ciclo |
| `HandoffBuilder.gs` | handoff generado por el sistema, con TTL, anti-replay y punteros (no corpus) |
| `PlanValidator.gs` | las 7 invariantes de la spec §7 sobre el **plan completo** |
| `ProviderAdapter.gs` / `OpenAIAdapter.gs` / `AnthropicAdapter.gs` | interfaz común y misma respuesta normalizada para ambos proveedores |
| `ToolBroker.gs` | contrato común de herramientas, guardas de alcance, tope de tool calls, retries acotados |
| `*ReadAdapter.gs` | lectura **real** de sólo lectura de Notion (incluida la query de data source del registro de decisiones), Asana, Drive y Calendar, siempre acotada a la partición del contexto |
| `SimulatedWriteAdapter.gs` | escritura exclusivamente simulada; no referencia ninguna superficie externa |
| `Orchestrator.gs` | el flujo completo y el contrato de salida mínimo |
| `Main.gs` | puntos de entrada **manuales** |

## 3. Instalación (sin activación)

1. Crea un proyecto de Apps Script **nuevo y aislado** del código productivo.
   No lo enlaces a ninguna hoja, documento ni formulario.
2. Activa el runtime V8 y sustituye el manifiesto por `appsscript.json` de este
   repositorio (Configuración del proyecto → *Mostrar `appsscript.json`*).
   Los scopes declarados son exactamente tres, todos de lectura:
   `script.external_request`, `drive.readonly`, `calendar.readonly`.
   **No hay scope de Gmail, ni de escritura de Drive/Calendar, ni
   `script.scriptapp`** — el proyecto ni siquiera está autorizado a crear un trigger.
3. Copia los 21 archivos de `src/` y los 10 de `tests/` como archivos `.gs`
   del proyecto. El orden de los archivos no importa: ningún módulo referencia
   a otro en tiempo de carga.
4. **No crees ningún trigger.** No abras *Activadores*. Nada de este proyecto lo
   necesita.
5. Ejecuta `runUnitTests` y `runAcceptanceCases` desde el editor. Ambas corren en
   Nivel 0 con fixtures sintéticos y **no piden ninguna autorización de red**.

En este punto el prototipo está instalado y probado, sin credenciales y sin
haber tocado ninguna fuente real.

## 4. Configuración (sólo si vas a Nivel 1/2)

Los nombres de las propiedades están en `script-properties.example.json`.
**Ese archivo documenta nombres; los valores se pegan a mano** en
Configuración del proyecto → *Propiedades del script*.

| Propiedad | Clave simbólica | Para qué |
| --- | --- | --- |
| `METIS_OPENAI_API_KEY` | `OPENAI_API_KEY` | invocar la Responses API |
| `METIS_ANTHROPIC_API_KEY` | `ANTHROPIC_API_KEY` | invocar la Messages API |
| `METIS_NOTION_API_KEY` | `NOTION_API_KEY` | lectura de Notion |
| `METIS_ASANA_API_KEY` | `ASANA_API_KEY` | lectura de Asana |
| `METIS_SOURCE_PARTITIONS` | `SOURCE_PARTITIONS` | qué contenedor de cada fuente pertenece a cada contexto |
| `METIS_PRICING` | `PRICING` | precio por 1.000 tokens, por proveedor y modelo |
| `METIS_LIMITS` | `LIMITS` | override opcional de los límites por corrida |
| `METIS_DECISIONS_DATA_SOURCE_ID` | `DECISIONS_DATA_SOURCE_ID` | data source del registro de decisiones (opcional) |

### Partición de fuentes por contexto

`METIS_SOURCE_PARTITIONS` es lo que hace real la separación de contexto: cada
consulta se acota **en origen** al contenedor declarado —data source de Notion
con filtro por `Proyecto`, proyecto de Asana, carpeta de Drive, calendario— en
vez de leer todo y descartar después. **Sin partición declarada para
(contexto, fuente), esa fuente no se lee.** Un objeto de otro contexto no es
"filtrado": es inalcanzable, y pedirlo por id lanza `PARTITION_VIOLATION`.

Nunca declares ahí el calendario primario productivo, ni los data sources de
CANON, Chat Log o Handoffs PHI: son superficies prohibidas.

### Precios y techos de gasto

No hay tabla de precios **ni cifras monetarias** en el código, y el guard G8 lo
impide. Dos razones distintas:

- **Precios:** cambian; uno obsoleto produce un contador de costo falso, que es
  peor que no tenerlo. Si falta el precio del modelo usado, el costo queda
  **desconocido** y la corrida falla cerrada en vez de estimar a ciegas.
- **Techos (`MAX_RUN_BUDGET_USD`, `MAX_DAILY_BUDGET_USD`,
  `MAX_MONTHLY_BUDGET_USD`):** cuánto está dispuesto a gastar el operador no es
  una decisión que pueda tomar el código. Un default plausible se convierte en
  el presupuesto real de todo el mundo sin que nadie lo haya decidido. Se
  declaran en `METIS_LIMITS`; **sin ellos, `OpenAIAdapter` y `AnthropicAdapter`
  rechazan la llamada** antes de tocar la red (`BUDGET_UNCONFIGURED`).

Los límites de política —intervenciones, tool calls, retries— sí llevan default,
porque son forma del ciclo, no dinero del operador.

Reglas que el código impone, no sólo documenta:

- el modelo recibe **nombres abstractos de capacidad** (`notion_read`, …), nunca
  el nombre de la propiedad ni su valor;
- ningún secreto entra a prompts, handoffs, ledger, fixtures, logs ni mensajes de
  error: todo error de proveedor pasa por `redactProviderError` antes de exponerse;
- `Config.gs` sólo contiene claves simbólicas.

### Subir de nivel

`Config.gs` arranca en `RUN_LEVEL = LEVELS.LEVEL_0`. Cambiarlo es una acción
manual y explícita:

- **Nivel 0** — simulación pura. Los adaptadores de lectura real están
  **bloqueados por código** (`LEVEL_VIOLATION`), aunque haya credenciales cargadas.
- **Nivel 1** — lectura real de sólo lectura contra las fuentes.
- **Nivel 2** — además, construcción y validación de planes de escritura. Toda
  acción externa sigue siendo simulada.

Nivel 3 está fuera de alcance y no existe en el código.

## 5. Corrida manual

Desde el editor:

```javascript
// Corrida sin proveedores: no llama a nada, sólo confirma que falta configuración.
runPrototype('¿Cuál es el estado actual del gate de Ciclo 1 a Ciclo 2 en Metis?');

// Corrida con proveedores reales (requiere Script Properties y Nivel 1/2):
runPrototypeLive('¿Cuál es el estado actual del gate de Ciclo 1 a Ciclo 2 en Metis?', {
  current_model: 'OPENAI',
  mandate: { source: 'LIVE_OPERATOR', requests_execution: false }
});

// Diagnóstico: informa PRESENCIA de cada propiedad, nunca su valor.
checkConfiguration();

// Poda del ledger por retención. Manual, nunca programada.
purgeLedger();
```

Opciones útiles de `Orchestrator.run(request, options)`:

| Opción | Efecto |
| --- | --- |
| `current_model` | `'OPENAI'` \| `'ANTHROPIC'` — dónde empieza la corrida |
| `providers` | `{OPENAI, ANTHROPIC}` con instancias de `ProviderAdapter` |
| `mandate` | `{source:'LIVE_OPERATOR'\|'FIXED_POLICY', requests_execution:boolean}` — abre (o no) la fase de acción |
| `operator_context` | contexto declarado por el operador, para desambiguar sin recuperar |
| `operator_model_instruction` | "trabaja esto con Claude/ChatGPT" |
| `requires_cross_audit` | fuerza auditoría cruzada |
| `capability_gap`, `exclusive_tool`, `continuity` | causas materiales de transferencia |
| `required_sources` | fuentes que deben tener cobertura para poder afirmar exhaustividad |
| `currency_root` | id de la decisión raíz cuya cadena de sustitución hay que cerrar |

Cada corrida devuelve el contrato de salida mínimo: `execution_id`, contexto,
intención, ruta y motivo, modelos y rol, fuentes consultadas, cobertura,
vigencia, evidencia con etiqueta epistémica, señales de riesgo, handoff,
`action_plan`, acciones con su simulación, bloqueos, ledger, límites y una
**única** respuesta final.

## 6. Tests

Dentro de Apps Script, a mano:

```javascript
runUnitTests();        // suites de la spec §17 + partición, vigencia, presupuesto y contratos
runAcceptanceCases();  // los 10 casos de la spec §16
runAllTests();         // todo, con veredicto PASS/FAIL

// Nivel 1: una lectura real acotada por (contexto, fuente), incluida
// calendar.read sobre una ventana corta. Sin modelos, sin plan, sin
// simulación, sin escritura. Requiere RUN_LEVEL = LEVEL_1.
smokeTestLevel1();                          // ventana de 7 días

// Con canarios: objetos que sabes que existen y que la fuente DEBE devolver.
smokeTestLevel1({
  contexts: ['METIS'],
  calendar_window_days: 1,
  canaries: {
    METIS: {
      'notion.search':    { query: 'gate', expect_id: '<page-id>' },
      'notion.decisions': { min_results: 3 },
      'asana.search':     { query: 'registrar', expect_title_contains: 'gobernanza' },
      'drive.search':     { query: 'notas' },
      'calendar.read':    { min_results: 1 }
    }
  }
});
```

Fuera de Apps Script, para poder **imprimir** los resultados sin desplegar:

```bash
node tools/run_local.js all           # unit | acceptance | all
node tools/run_local.js all --verbose # con el detalle de cada assert
node tools/static_guards.js           # guards estáticos sobre el código que se sube
```

`tools/` es un arnés de ejecución local: concatena los mismos `.gs` y los evalúa
con los globals de Apps Script emulados. **No se sube al proyecto, ningún `.gs`
lo importa y no cambia el runtime.** Durante los tests bloquea `UrlFetchApp`,
`DriveApp`, `CalendarApp`, `GmailApp` y `ScriptApp`: cualquier intento de salida
externa lanza excepción. Ver `ENTREGA.md` §7 para el detalle de esta desviación.

### Veredicto del smoke test

Tres estados, y **cero resultados no es PASS**:

| Estado | Cuándo |
| --- | --- |
| `PASS` | la fuente devolvió al menos un resultado real admitido, y el canario declarado apareció |
| `NO_DEMOSTRADO` | no hubo error, pero tampoco evidencia: 0 resultados, o menos de `min_results` |
| `FAIL` | la lectura falló, hubo descarte por contexto, o la fuente respondió **sin** el canario esperado |
| `OMITIDA` | sin partición declarada: no se lee, por diseño |

`report.status` es `FAIL` si algo falló; si no, `NO_DEMOSTRADO` cuando queda
alguna fuente sin demostrar **o cuando no se validó ninguna**; `PASS` sólo si
todo lo leído quedó demostrado. `report.ok` es `true` únicamente con `PASS`.

Por qué importa la distinción: una fuente vacía, una partición que apunta a un
contenedor equivocado, un filtro por `Proyecto` que no casa y una credencial que
no ve nada **devuelven todas cero**, y sólo la primera es aceptable. El canario
—un objeto que el operador sabe que existe— es lo que separa "leí y no hay nada"
de "no estoy leyendo lo que creo". Que una fuente responda pero sin el canario es
`FAIL`, no `NO_DEMOSTRADO`: está leyendo, y no lo que se creía.

## 7. Cero triggers, cero escrituras productivas

Comprobado, no sólo declarado (`node tools/static_guards.js`):

| Guard | Qué demuestra |
| --- | --- |
| G1 | ninguna referencia ejecutable a `ScriptApp`, ningún `newTrigger`, ninguna función `onOpen`/`onEdit`/`doGet`/`doPost` |
| G2 | ninguna referencia ejecutable a `GmailApp`; ningún método de mutación de `DriveApp`/`CalendarApp` |
| G3 | ningún verbo HTTP `PUT`/`PATCH`/`DELETE` en ningún adaptador |
| G4 | `UrlFetchApp` sólo en los cuatro adaptadores autorizados |
| G5 | `SimulatedWriteAdapter.gs` no referencia ninguna superficie externa |
| G6 | `appsscript.json` declara exactamente tres scopes, todos de lectura |
| G7 | ningún literal con forma de secreto en código ni fixtures |
| G8 | ninguna tabla de precios ni techo de gasto embebido; ambos adaptadores exigen techos declarados antes de llamar |
| G9 | todo adaptador de lectura exige partición declarada antes de leer |

El análisis se hace sobre el código con comentarios y literales de cadena
eliminados, de modo que la tabla documental `WOULD_CALL` (que menciona
`GmailApp.sendEmail(...)` como texto) no produzca falsos positivos ni tape una
referencia real. Los guards están verificados en negativo: al inyectar
deliberadamente un trigger, una llamada a Gmail y un scope de escritura fallan
G1, G2, G5 y G6; al embeber un precio y quitar la guarda de partición de un
adaptador fallan G8 y G9.

## 7 bis. Vigencia: cómo se decide

El registro de decisiones da **dos** señales y no siempre coinciden: la relación
`Sustituida por` y el select `Estado` (`vigente` | `modificada` | `derogada`).
La regla implementada, contrastada contra los datos reales:

- **la relación manda** cuando el `Estado` falta — está vacío en la mayoría de
  las filas reales, así que el vacío se resuelve por el default declarado en la
  propia propiedad ("por defecto vigente") y la respuesta lo **dice**;
- **cuando ambas señales hablan y se contradicen** —una fila marcada `vigente`
  que sí tiene sustituta— no manda ninguna: es `CURRENCY_CONFLICT` y la corrida
  para en `REQUIRES_ANDRES`;
- la cadena se recorre por `Sustituida por` hasta la terminal. Si falta un
  eslabón, hay ciclo, o alguna decisión de la cadena está en conflicto, la
  cadena **no cierra** y no se afirma estado.

## 8. Límites de la corrida

Configurables en `Config.LIMITS`. Alcanzarlos produce **parada dura**, nunca
auto-ampliación:

| Límite | Default |
| --- | --- |
| intervenciones de modelo | 4 — un ciclo cerrado: productor (lectura + producción) y auditor (lectura + veredicto) |
| tool calls | 16 |
| retries de lectura | 2 |
| presupuesto por corrida | **sin default** — `METIS_LIMITS` |
| techo diario / mensual | **sin default** — `METIS_LIMITS` |
| TTL de handoff | 15 min |
| retención del ledger | 24 h |

## 9. Qué NO hace este prototipo

No escribe en Notion, Asana, Calendar, Drive ni Gmail. No modifica el CANON ni
ninguna fuente productiva. No crea triggers, schedulers, webhooks, servidores,
bases de datos, colas, vector stores ni memoria persistente de negocio. No crea
superficies físicas de sandbox. No usa el ledger como fuente de verdad de Metis.
No se despliega ni se publica.

Completar este prototipo **no autoriza Nivel 3 ni producción**.
