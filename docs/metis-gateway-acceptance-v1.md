# Metis Orchestration Gateway v1 — aceptación

## Declaración PIE

Sesión canónica. Techo técnico declarado: 7/10. CANON v2.6, actualizado el
15-ago-2026, consultado en Drive; Notion y los servicios desplegados consultados
en vivo. Asana no se utilizó ni se acredita su conectividad en este cierre.
La decisión de 31-ago que levanta la moratoria se verificó en Notion.
Los hechos siguientes proceden del cliente real, código, pruebas y despliegues.
El éxito funcional no demuestra por sí solo valor neto sostenido ni disponibilidad.

## Resultado

**Aceptación funcional: CUMPLIDA.** Solicitud natural en Claude → MCP autenticado
→ cola durable → puente HMAC → Orchestrator v5 → respuesta íntegra en el mismo
turno. No fue necesario operar Apps Script, elegir modelos ni copiar un PHI durante
la corrida. Las pruebas se enviaron desde la sesión de Andrés bajo su autorización.

- Cliente: [conversación de aceptación](https://claude.ai/chat/d18ef5b9-a36f-45dc-b7e4-54f976d091d9).
- Endpoint: `https://missingmcp-production.up.railway.app/metis/mcp`.
- Rama: `metis/orchestration-gateway-v1`.
- Código funcional final: `1096450de34e20cd1c474130f977b502519cd7ae`.
- Despliegue funcional acreditado: `a63333b1-1199-4b3c-8345-d3dd3dc75230`.
- Puente: proyecto `1kO_QM5HgBgG10O1wCTIuqeHgB_GfFRrPXyNnGf4_xr6LrzNsuZbchMBZ`,
  publicación v2, biblioteca Engine v5. Diagnósticos manuales adicionales en HEAD.
- Baseline del motor: `metis-orchestrator-baseline-v1.0`,
  SHA `56c7361ad9a7c9c1480c0ee202b7e48a3e5cecd4`, sin modificaciones.
- Tag previsto de congelación: `metis-orchestration-gateway-baseline-v1.0`.
  Se crea únicamente después de CI y despliegue final verificados; su anotación
  conserva el SHA y los identificadores finales sin autorreferencia en este archivo.

## Corrida final y reintento

Objeto: auditar un borrador hipotético que atribuye quince consultas a dos fases
de cinco y una tercera sin herramientas. Outcome: rechazar el borrador, corregir
el máximo a diez y excluir búsquedas en la fase final. No modifica gobernanza.

| Evidencia | Valor |
|---|---|
| Gateway execution / correlation | `f1aa947d-f7ac-4f0e-bdea-4aaa4ede11ed` |
| Orchestrator execution | `6ee678d1-2a74-437e-80fc-f0613861d3b9` |
| Clave conservada | `METIS_GATEWAY_AUDIT_20260914_04` |
| Creación, Unix segundos | `1789452288.7193708` |
| Estado / ruta | COMPLETED / CROSS_AUDIT |
| Proveedores | ANTHROPIC → OPENAI → reconciliación |
| Handoff interno | `c77811ad-49ac-4bec-9a34-cd4c4d8102ca`, reconciled=true |
| Costo informado por motor | US$0.23199, cost_known=true |
| Consumo | 3/9 intervenciones; 0/32 herramientas; 0 reintentos |
| Auditor | 1 turno, sin lecturas externas por caso autocontenido |
| Resultado | 2862 caracteres; sin degradación, bloqueo ni revisión pendiente |
| Cliente | Un create y siete consultas automáticas; respuesta final en el mismo turno |

Tras desplegar 1096450 y reiniciar Railway, Claude repitió create con los mismos
argumentos. Comparación de los JSON extraídos del DOM de las herramientas:
`sameRequest=true`, `sameResult=true` mediante JSON.stringify. Misma fecha,
ID Gateway, ID motor, respuesta y costo. No nueva ejecución pagada.
La respuesta original está íntegra en [respuesta de aceptación](metis-gateway-final-answer.md).

## Matriz de aceptación del mandato

| Criterio | Evidencia / resultado |
|---|---|
| 1–6, entrada desde conversación sin operación técnica | Solicitud escrita en Claude; invocación MCP y consultas automáticas. PASS |
| 7–10, identidad, motor y persistencia | UUID independiente, Engine v5, resultado recuperado después de redespliegue. PASS |
| 11–12, una respuesta final al flujo | Corrida final sin mensaje humano de seguimiento; respuesta íntegra. PASS |
| 13, trazabilidad | IDs Gateway/motor, timestamp, handoff y costo conservados. PASS |
| 14, idempotencia | Repetición real después de reinicio con JSON idéntico; pruebas de concurrencia y conflicto. PASS |
| 15, secretos | OAuth/PKCE, firma HMAC, cifrado durable, logs de transporte suprimidos; pruebas de aislamiento y contenido. PASS en alcance probado |
| 16, presupuesto | Engine intacto; suite económica/9 guardas y topes reales conservados. PASS |
| 17, resultado sustantivo | Primera corrida REQUIRES_ANDRES/ABSTAIN, costo cero, recibida y recuperada sin repetición. PASS |
| 18, fallos técnicos | Incidente 4 FAILED/ENGINE_INTERRUPTED visible, pausa durable y costo conciliado con evidencia; fallo original conservado. PASS |

Tests mínimos A–P cubiertos por tests/test_metis.py, test_bridge.cjs y suites del
motor: payload/auth, identidad/conflicto, ciclo, persistencia cifrada, desconexión,
fallos/costo incierto, recuperación manual, integridad de respuesta y guardas.
CI de 1096450: **352 Python, 12 puente, 168 motor / 898 assertions, 9 guardas PASS**.
[CI acreditada](https://github.com/andrespadvaz-netizen/missingmcp/actions/runs/34935664685).
La suite completa vuelve a ejecutarse sobre el commit documental de congelación.

## Historial de pruebas y costo

| Caso | Estado / alcance | US$ |
|---|---|---:|
| 1, contexto ausente | REQUIRES_ANDRES / ABSTAIN; idempotencia después de reinicio | 0 |
| 2, simple | COMPLETED / LOCAL; una intervención | 0.032585 |
| 3, primera auditoría cruzada | COMPLETED; 9 intervenciones, 21 herramientas, auditor bloqueante; recuperada mediante seguimiento | 0.36720625 |
| 4, interrupción | FAILED; costo estimado del proveedor conciliado una vez | 0.072375 |
| 5, hipótesis 3×4 | COMPLETED / LOCAL; cliente había omitido la acción de auditar | 0.05671 |
| 6, aceptación final 2×5 | COMPLETED / CROSS_AUDIT; retorno automático; reintento idéntico | 0.23199 |
| Total de aceptación registrado/estimado | Excluye tarifa de infraestructura y suscripción del cliente | **0.76086625** |

Gasto adicional de este cierre: US$0.28870. Son importes del motor y una
conciliación por tokens, no factura consolidada definitiva de los proveedores.
No se aumentaron presupuestos ni se creó un servicio de pago nuevo.

## Hallazgos corregidos y límites reales

- El caché Google requería MIME gzip explícito: se recuperó el resultado original.
- Se suprimieron URLs firmadas efímeras en el contexto de transporte de logs HTTP.
- Las consultas esperan hasta 20 segundos para evitar cierres prematuros del cliente.
- Una interrupción con costo desconocido pausa nuevo gasto; la revisión contable
  manual exige ID/secuencia/hash y contadores verificados, preservando el fallo.
- La primera revisión de una pausa ya es inmediata incluso en un host recién iniciado.
- Se aclaró al cliente que preserve la acción y restricciones del usuario: la quinta
  prueba omitió «auditar» y cayó en LOCAL. El motor decide sobre el texto recibido;
  la fidelidad del cliente sigue siendo una dependencia, no una garantía determinista.
- La causa de la interrupción Google de la cuarta corrida sigue desconocida. Google
  mostró UNKNOWN. La recuperación del costo no convierte esa corrida en éxito.
- La última corrida no necesitó retrieval; la tercera sí lo acreditó. No confundir
  ambos alcances ni afirmar lectura independiente en la última.
- Claude añadió una salvedad sobre registro PHI del handoff interno. El motor generó
  y reconcilió su objeto interno; eso no prueba registro en Notion. Este caso
  autocontenido no transfiere un entregable canónico (§2.3.3). No se registra como
  auditoría de arquitectura ni se afirma que el Gateway reemplaza el PHI canónico.
- LEVEL_2 simula escrituras: la aceptación no habilita escrituras productivas autónomas.
- Una sola réplica/volumen y Apps Script siguen siendo dependencias operativas.
  Un costo incierto puede exigir conciliación humana; no hay repetición pagada automática.

## Operación y revisión

Instrucción de uso: «Usa Metis. Contexto: Metis. Audita [objeto y resultado esperado]».
Para consultas simples puede sustituirse «Audita» por la acción deseada. El usuario
no elige proveedor ni administra claves de idempotencia. El cliente debe conservar
la solicitud y esperar el estado terminal. Ante FAILED, consulta el mismo ID.

Configuración, reconstrucción y rollback: [documento operativo](metis-gateway-v1.md).
Nunca restaurar una cola antigua ni borrar recibos para reabrir gasto.
Revisión de valor: 13-oct-2026, cuatro semanas tras esta aceptación; retirar o
simplificar si no reduce operación técnica de Andrés o cuesta más mantenerlo que
el valor observado. Esta fecha documenta la revisión; no crea una automatización.
Fuera de alcance: segundo cliente, hardware, memoria vectorial, routing por costo,
escrituras productivas autónomas y ampliación del motor.
