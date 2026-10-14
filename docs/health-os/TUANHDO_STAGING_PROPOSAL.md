# Propuesta de staging aislado para HealthExport

Estado: diseño y pruebas sintéticas. No se conecta a producción, no
modifica el core histórico ni habilita ingesta.

## Identidad y conservación

1. Conservar cada fila exportada en staging **inmutable**, identificada
   internamente por `(batch_id, record_index)`. `batch_id` identifica
   una recepción, no una muestra HealthKit. Cifrar el payload y no
   registrar valores ni nombres de fuente en recibos o logs.
2. Guardar una huella `derived-health-export:v1:<sha256>` calculada con
   tipo, intervalo UTC, valor, unidad efectiva, fuente, paquete y
   dispositivo. Es una pista para comparar reenvíos; **no equivale a un
   UUID de origen**.
3. Si dos filas del mismo lote tienen campos exportados idénticos,
   conservar ambas con índices distintos y marcar
   `indistinguishable_records`. No sumarlas ni sobrescribir una con
   metadatos de la otra.
4. Si dos filas con huellas distintas colisionarían en la clave actual
   de Health OS, marcarlas `core_key_collision` y retenerlas fuera de
   la ingesta. La clave histórica y sus registros quedan intactos.
5. Un reexport puede señalar candidatos a duplicado por huella; no
   borrar ni fusionar filas hasta verificar identidad de origen o
   adoptar una política explícita de equivalencia.

## Selección y cálculo, aún no activados

- La **Política Operativa v0.5, sección 3.3**, ya fija Apple Watch vía
  Apple Health como fuente única de FC en reposo, HRV, sueño,
  respiración, saturación y entrenamientos. Speediance solo aporta
  detalle de fuerza enlazado. Presión, glucosa, peso, cintura y
  temperatura son mediciones manuales con equipo definido en 3.5;
  para peso exige la misma báscula y condiciones. No se volverá a
  pedir una preferencia general por métrica.
- La coincidencia técnica deberá asociar `sourceBundleId`, modelo y
  versión de software con la fuente aprobada. Texto libre `source`
  o un dispositivo nulo no acreditan Apple Watch. La política 3.3
  excluye registros sin esos campos. Sin verificación, la métrica
  permanece no computable y no mejora Readiness.
- Mantener por separado productores concurrentes. Si hay intervalos
  superpuestos para una métrica y no existe regla validada, excluir
  esos intervalos del cálculo y mostrar cobertura incompleta. No
  formar totales a partir de filas de productores distintos.
- Para sueño, usar intervalos semiabiertos `[inicio, fin)`. El
  código 2 (despierto) nunca suma. Sólo podrá unirse la duración de
  intervalos de fases **iguales y del mismo productor verificado**;
  intervalos de fases distintas, fuente desconocida o mezcla de
  productores quedan retenidos para revisión. El parser actual
  marca cualquier solapamiento y no calcula un total.
- `heartRate` es pulso ordinario; jamás se relabela como
  `resting_heart_rate`. Unidades, booleanos, nulos, no finitos y
  offsets se validan antes de siquiera proponer una muestra.

## Alternativas y decisiones

Un productor HealthKit que suministre el UUID original resolvería mejor
la identidad; el JSON disponible no lo garantiza. Mientras tanto,
staging inmutable más huella derivada evita pérdida silenciosa, pero
no demuestra que dos filas iguales sean una sola muestra física.

La decisión de fuente principal fisiológica **ya está tomada**.
Solo quedaría una decisión del titular si se quisiera admitir una
fuente nueva o identificar la báscula concreta para mediciones de
peso; este prototipo no necesita ninguna de las dos. El hueco actual
es técnico: verificar identificadores, modelo y versión de software
del export contra la fuente aprobada, sin inferirlos de un nombre
libre. La regla de colisiones, los límites de tamaño y la
conservación de evidencia no cambian la política clínica existente.

Antes de habilitar escritura persistente: validar un export con datos
redactados y consentimiento específico, el esquema de staging,
recibos sin PHI, idempotencia entre lotes y un plan de reversión.
