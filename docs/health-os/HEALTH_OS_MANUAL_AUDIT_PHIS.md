# Health OS: PHIs manuales B–E

Cada respuesta debe ser adversarial, read-only, con evidencia, veredicto y recibo.

## PHI B — migración

```text
Contexto: PERSONAL / Metis Health OS. Audita adversarialmente la migración.
Garmin es sólo histórico, sin bridge, login, worker ni fuente forward.
Apple Health es autoridad de fisiología/actividad y Speediance de fuerza.
Busca dependencia Garmin, mezcla no comparable, doble conteo o granularidad inventada.
Apple/Speediance se enlazan sólo con actividad compatible, inicio <=15 min y solapamiento >=50%.
Devuelve FAIL/PASS/REQUIRES_ANDRES, hallazgos, pruebas faltantes y recibo. No escribas nada.
```

## PHI C — guardas médicas

```text
Contexto: PERSONAL / Metis Health OS. Intenta demostrar inseguridad médica.
El sistema puede emitir ACTIVA, AJUSTA, DETEN, ABSTENERSE o ESCALAR, pero nunca diagnostica, prescribe, cambia medicación ni ejecuta cambios de entrenamiento.
Revisa sueño, HRV, FC, SpO2, respiración, presión, ECG, síntomas y Brief.
Exige abstención con evidencia insuficiente y escalamiento ante señales agudas.
Devuelve guardas faltantes, redacción prohibida, veredicto y recibo. Read-only.
```

## PHI D — calidad y privacidad

```text
Contexto: PERSONAL / Metis Health OS. Intenta romper procedencia, unidades, timezone, idempotencia, dedupe, baseline por métrica, stale data, mezcla de fuentes, faltantes y model_interpreted.
ChatGPT Health es consulta, no evidencia canónica. Comprueba que el Brief no expone datos crudos.
Devuelve casos de prueba, hallazgos, veredicto y recibo. Read-only.
```

## PHI E — PAC de cierre

```text
Contexto: PERSONAL / Metis Health OS. Demuele el DoD completo. Distingue IMPLEMENTADO, PROBADO, LIMITACIÓN, DEUDA y DECISIÓN PENDIENTE.
Comprueba arquitectura, Garmin, Apple/Speediance, transporte, Brief, guardas, tests, secretos, rollback, documentación y auditorías.
PASS sólo con evidencia de cada criterio; si falta transporte crudo o auditoría independiente, indica el bloqueo exacto. Read-only.
```

## Recibo

Registrar fecha, auditor, PHI exacto, fuentes, veredicto, hallazgos, limitaciones y respuesta íntegra.
