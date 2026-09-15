# Gateway: continuidad del contexto en Claude

## Resultado — 15-sep-2026

La integración instruye a Claude a incorporar en cada solicitud el proyecto y los
hechos pertinentes ya disponibles en la conversación o instrucciones visibles del
proyecto. Andrés puede pedir «Usa Metis para auditar esta propuesta» sin volver a
copiar el contexto conocido. Si falta información o hay ambigüedad real, Claude
debe hacer una pregunta concreta antes de ejecutar.

La última selección explícita de proyecto prevalece. El nombre de la herramienta
Metis no identifica por sí solo el proyecto. No se importa contexto de otros chats,
no se combinan proyectos y no se manda el historial completo. El texto enriquecido
se conserva exactamente para los reintentos con la misma clave.

## Implementación y validación

- Cambio de descripción y esquema descriptivo MCP en `src/missingmcp/adapters/metis.py`.
- Código: `df284e4eb222f3444f57cf84f00d95db49cca4a6`.
- Despliegue activo verificado.
- [CI exitosa](https://github.com/andrespadvaz-netizen/missingmcp/actions/runs/34937520782).
- 31 pruebas locales del adaptador PASS.
- Motor v5, puente v2, límites, cola y campos obligatorios intactos.
- El tag baseline anterior permanece en su commit original; esta mejora es posterior.

Se usaron exclusivamente ejemplos ficticios autocontenidos, sin consultas externas
ni modificaciones de registros por el motor.

| Caso | Solicitud y evidencia | Resultado |
|---|---|---|
| Contexto previo | Primer mensaje establece proyecto A, 4 paquetes por caja y 6 tarjetas por paquete. La petición siguiente sólo menciona «tres cajas de ese ejemplo». Claude incorpora proyecto y operandos. | PASS: 72 tarjetas, COMPLETED / LOCAL, sin pedir repetir datos. |
| Cambio de proyecto | Se establece proyecto B con 2 grupos de 5 fichas. La petición siguiente sólo menciona «ese nuevo ejemplo». Claude incorpora únicamente proyecto B y los nuevos operandos. | PASS: 10 fichas, COMPLETED / LOCAL, sin arrastrar proyecto A. |
| Ambigüedad | El usuario declara que no se ha determinado si el nuevo caso pertenece a proyecto A o proyecto B. | PASS: una aclaración antes de invocar Metis; no se creó ejecución. |

Ambas ejecuciones completadas: costo conocido, una intervención del modelo, cero
herramientas y cero escrituras. Los identificadores de ejecución, la conversación
y los costos se conservan en el registro privado de evidencia.

## Límites y criterio de uso

Techo técnico 7/10: son pruebas observadas de comportamiento del cliente, no una
garantía determinista. Las instrucciones permiten usar contexto visible del
proyecto, pero esa variante no se verificó separadamente en estas pruebas.
El Gateway no recibe automáticamente otros chats ni metadatos ocultos de Claude.
La continuidad se realiza al preparar la solicitud, sin memoria global adicional.

La respuesta de aclaración usó la palabra «registro», aunque no hubo escritura ni
llamada al motor; no debe interpretarse como autorización de registro. Claude
resumió las respuestas numéricas; estas pruebas acreditan contexto y resultado,
no reproducción literal de todos los metadatos del motor.

Se mantiene la revisión de valor del 13-oct-2026: evaluar si reduce la operación
técnica de Andrés. Esta corrección no crea un protocolo ni una automatización.
