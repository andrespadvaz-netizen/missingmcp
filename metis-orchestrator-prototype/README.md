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
| `RetrievalPolicy.gs` | precedencia de fuentes, cierre de cadena de vigencia, cobertura, etiquetas epistémicas, detección de prompt injection |
| `AuthorityPolicy.gs` | techo predeclarado, fase de lectura, fase de acción (sólo estrecha), procedencia de destino |
| `Router.gs` | LOCAL por default; transferencia sólo por causa material; cierre de ciclo |
| `HandoffBuilder.gs` | handoff generado por el sistema, con TTL, anti-replay y punteros (no corpus) |
| `PlanValidator.gs` | las 7 invariantes de la spec §7 sobre el **plan completo** |
| `ProviderAdapter.gs` / `OpenAIAdapter.gs` / `AnthropicAdapter.gs` | interfaz común y misma respuesta normalizada para ambos proveedores |
| `ToolBroker.gs` | contrato común de herramientas, guardas de alcance, tope de tool calls, retries acotados |
| `*ReadAdapter.gs` | lectura **real** de sólo lectura de Notion, Asana, Drive y Calendar |
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
| `METIS_ASANA_WORKSPACE_GID` | `ASANA_WORKSPACE_GID` | workspace del typeahead |
| `METIS_CALENDAR_READ_IDS` | `CALENDAR_READ_IDS` | calendarios legibles (**nunca el primario productivo**) |
| `METIS_DRIVE_READ_FOLDER_IDS` | `DRIVE_READ_FOLDER_IDS` | carpetas acotadas (reservado) |

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
| `currency_root` | decisión raíz cuya cadena de vigencia hay que cerrar |

Cada corrida devuelve el contrato de salida mínimo: `execution_id`, contexto,
intención, ruta y motivo, modelos y rol, fuentes consultadas, cobertura,
vigencia, evidencia con etiqueta epistémica, señales de riesgo, handoff,
`action_plan`, acciones con su simulación, bloqueos, ledger, límites y una
**única** respuesta final.

## 6. Tests

Dentro de Apps Script, a mano:

```javascript
runUnitTests();        // 7 suites de la spec §17 (+ contrato de proveedor y adaptadores)
runAcceptanceCases();  // los 10 casos de la spec §16
runAllTests();         // todo, con veredicto PASS/FAIL
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

El análisis se hace sobre el código con comentarios y literales de cadena
eliminados, de modo que la tabla documental `WOULD_CALL` (que menciona
`GmailApp.sendEmail(...)` como texto) no produzca falsos positivos ni tape una
referencia real. Los guards están verificados en negativo: al inyectar
deliberadamente un trigger, una llamada a Gmail y un scope de escritura, G1, G2,
G5 y G6 fallan.

## 8. Límites de la corrida

Configurables en `Config.LIMITS`. Alcanzarlos produce **parada dura**, nunca
auto-ampliación:

| Límite | Default |
| --- | --- |
| intervenciones de modelo | 3 (productor con Retrieval + producción, y auditor) |
| tool calls | 12 |
| retries de lectura | 2 |
| presupuesto por corrida | 0.50 USD estimados |
| techo diario / mensual | 5 / 25 USD estimados |
| TTL de handoff | 15 min |
| retención del ledger | 24 h |

## 9. Qué NO hace este prototipo

No escribe en Notion, Asana, Calendar, Drive ni Gmail. No modifica el CANON ni
ninguna fuente productiva. No crea triggers, schedulers, webhooks, servidores,
bases de datos, colas, vector stores ni memoria persistente de negocio. No crea
superficies físicas de sandbox. No usa el ledger como fuente de verdad de Metis.
No se despliega ni se publica.

Completar este prototipo **no autoriza Nivel 3 ni producción**.
