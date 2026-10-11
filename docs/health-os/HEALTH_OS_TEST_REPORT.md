# Health OS: reporte de pruebas

Fecha de ejecución: 2026-10-11 (Windows).

## Resultado

| Alcance | Resultado |
|---|---|
| `pytest -q tests/test_health_os.py` | PASS: 15 passed en 1.05 s |
| `git diff --check` para cambios Health OS | PASS |
| Suite completa | BLOQUEADA por una aserción POSIX preexistente de permisos Garmin en Windows |

La suite completa ya supera la disponibilidad de zona horaria al incluir
`tzdata`. La aserción de modo `0600` de un token Garmin quedó limitada a POSIX,
porque Windows no representa ACLs mediante esos bits. Health OS permanece
independiente de esa ruta histórica.

## Cobertura Health OS

- idempotencia y procedencia;
- conversiones de unidades y rechazo de unidades incompatibles;
- sesiones lógicas Apple Health + Speediance sin pérdida de registros brutos;
- no fusión cuando no hay solapamiento;
- eras Garmin/Apple y 14 días de baseline;
- detección de legado post-transición;
- cálculo de día local;
- validación de payload y rechazo de Garmin en ingestión nueva.
- baseline específico por métrica, abstención por cobertura incompleta y
  exclusión de valores `model_interpreted` de decisiones.

La auditoría cruzada con Claude no se ejecutó como condición de aprobación:
está diferida hasta que el Gateway habilite lecturas completas, conforme a la
instrucción vigente.
