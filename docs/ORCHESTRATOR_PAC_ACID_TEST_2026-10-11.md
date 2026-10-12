# PAC de Demolición y prueba del ácido — Orchestration Gateway

Fecha: 2026-10-11  
Alcance: Gateway Railway, cola durable, puente Apps Script, motor y frontera de escrituras.

## Declaración anticipada PIE

- Techo técnico previo: 8/10 en seguridad y 6/10 en latencia operativa.
- Riesgo principal: que una capa de control correcta convierta el PHI automático en un flujo más lento y frágil que el proceso manual.
- Criterio de aprobación: una sola ruta normal de ejecución, recuperación explícita, idempotencia durable, costo trazable, aislamiento de contexto, pruebas completas y aceptación real terminal.

## Demolición

1. **Dos conductores concurrentes.** Railway avanzaba los checkpoints y el puente programaba además un trigger de Apps Script. Ambos competían por `ScriptLock`. Se elimina el trigger de la ruta normal; queda únicamente en `repairBridgeRuntime()` como recuperación explícita.
2. **Impuesto fijo por checkpoint.** El worker dormía cinco segundos después de cada paso. La cadencia activa pasa a 100 ms; `BUSY`, transporte e inactividad conservan backoff de un segundo.
3. **Contexto de escritura confiado a una sola capa.** El plan verificaba coherencia interna, pero Railway no ligaba explícitamente `resolved_context` con `metis_executions.context_project`. Ahora una discrepancia invalida todo el plan antes de persistir intenciones.
4. **Latencia sin separación de cola.** El recibo expone `dispatched_at` y `queue_wait_ms`, además de los tiempos de checkpoint del puente, para separar cola, proveedor y propagación.
5. **Fixtures obsoletos.** Las pruebas de escritura y del encabezado heredado omitían el contexto obligatorio o lo contradecían. Se alinearon con el contrato productivo.

## Prueba del ácido

- Puente y escrituras: 28/28 controles pasan, incluidos pérdida de respuesta, reinicio, replay, corte de red, recibo incierto, conciliación por readback y contabilidad conservadora.
- Contrato Lens: PASS.
- Motor: 185/185 casos, 1,045 aserciones, PASS.
- Guardas estáticas: 9/9 PASS.
- Recuperación terminal: 20/20 PASS.
- Política y adaptador productivo: 26/26 PASS.
- Suite Python específica del Gateway: 41/41 PASS antes de agregar el enlace de contexto; 17/17 pruebas de escrituras pasan después del enlace.
- Suite Python completa recorre 424 casos; la ejecución en Windows llega al tramo final sin fallos visibles, pero el proceso anfitrión no imprime el resumen de salida. Las suites afectadas por este cambio se ejecutaron por separado y pasan.

## Invariantes conservadas

- Una clave idempotente nunca crea una segunda corrida.
- Un recibo `STARTED` no se vuelve a despachar.
- Un resultado incierto no autoriza replay.
- Las pausas se acotan al contexto; las escrituras inciertas se pausan globalmente por superficie.
- La ruta de recuperación nunca suplanta la ruta normal.
- Ninguna acción se escribe si el contexto durable y el contexto resuelto difieren.
- `REQUIRES_ANDRES` por contradicción de vigencia es un resultado de política, no una falla técnica.

## Riesgo residual honesto

No existe garantía absoluta frente a caídas de Google, Railway o proveedores. El diseño garantiza que esos fallos sean terminales o reconciliables, con costo conservador, sin duplicar efectos y sin contaminar contextos. La latencia mínima queda limitada por las llamadas reales a modelos y APIs externas.

