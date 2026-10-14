# TuAnhDo HealthExport: contrato de lectura local

Estado: prototipo **solo de lectura**, construido sobre la copia de
`75b2762`. No hay endpoint, envío HTTP, escritura de base de datos,
credenciales ni conexión con el Brief. El objeto `summary` del export
se conserva fuera del cálculo: no reemplaza muestras.

## Entrada y salida

`parse_health_export(payload)` acepta un objeto JSON ya cargado con
`aggregation: "raw"`, `records` y `workouts`. Los campos de cabecera
`exportDate`, `source`, `app`, `timeZone`, `dateFormat`, `dateRange`,
`units` y `summary` no son evidencia clínica por sí mismos. Cada registro
admitido exige `type` (o `name`), `startDate`, `endDate`, valor y unidad
explícita, salvo sueño. Las fechas requieren offset UTC. La unidad puede
estar en el registro o en `units[type]`.

La salida `DryRun` conserva cada muestra por separado con inicio, fin,
tipo, valor, unidad, fuente, identificador de paquete, dispositivo y detalle
de dispositivo. `raw_records` conserva en memoria también filas de tipos
desconocidos o inválidos; no las convierte en muestras computables. El
recibo `receipt()` solo comunica conteos y códigos
fijos de incidencias: nunca incluye valores, fechas, nombres de fuente,
claves arbitrarias ni huellas. Todas las muestras quedan `computable=False`.
El parser limita el JSON ya cargado a 16 MiB serializados, 50,000
registros y 10,000 entrenamientos. Una futura ruta HTTP tendría que
aplicar el límite de bytes **antes** de cargar el JSON.

| Tipo exportado | Interpretación candidata | Unidad admitida |
| --- | --- | --- |
| `stepCount` | `steps` | `count` |
| `distanceWalkingRunning` | `distance` | `km` |
| `activeEnergyBurned` | `active_energy` | `kcal` |
| `heartRate` | `heart_rate`, **no** FC en reposo | `count/min` o `bpm` → `bpm` |
| `restingHeartRate` | `resting_heart_rate`, sin derivarlo del pulso ordinario | `count/min` → `bpm` |
| `heartRateVariabilitySDNN` | `hrv` | `ms` |
| `respiratoryRate` | `respiratory_rate` | `count/min` → `breaths/min` |
| `walkingHeartRateAverage` | `walking_heart_rate_average` | `count/min` → `bpm` |
| `appleExerciseTime`, `appleStandTime` | `exercise_time`, `stand_time` | `min` |
| `flightsClimbed` | `flights_climbed` | `count` |
| `environmentalAudioExposure` | `environmental_noise` | `dBASPL` → `dBA` |
| `walkingSpeed`, `stairAscentSpeed`, `stairDescentSpeed` | velocidades distintas | `km/h` |
| `walkingStepLength` | `step_length` | `m` → `cm` (×100) |
| `bodyMass` | `weight` | `kg` |
| `sleepAnalysis` | intervalo de sueño, sin suma automática | códigos 2 despierto, 3 core, 4 deep, 5 REM; `valueName` debe concordar |

El catálogo del segundo JSON informado incluye 26 tipos. `oxygenSaturation`,
`walkingAsymmetryPercentage` y `walkingDoubleSupportPercentage` se retienen
como `percentage_semantics_unverified`: `%` no aclara si el número fue
exportado como fracción o como porcentaje. No se usa su magnitud para elegir
escala. `appleSleepingBreathingDisturbances`, `basalEnergyBurned`,
`physicalEffort` y `timeInDaylight` no tienen equivalencia de cálculo
aprobada; temperatura de muñeca queda fuera por la política de mediciones
manuales. `appleStandHour` se conserva como categoría sin convertirla en
minutos ni inventar otros códigos. Las categorías desconocidas se retienen
con una incidencia fija. Ninguno de esos tipos entra al core.

Los intervalos de cantidad pueden ser puntuales (`startDate == endDate`),
como sucede con pulso y peso en el export validado. Sueño exige duración
positiva. Pasos, distancia, energía activa, minutos de ejercicio y de pie,
y pisos subidos con inicio igual a fin se
conservan pero quedan `additive_point_no_duration` y no computables.
Los intervalos invertidos siempre se rechazan.

Tipos desconocidos, unidades ausentes o incompatibles, valores nulos,
booleanos, negativos o no finitos y fechas sin offset se reportan mediante
códigos. Los entrenamientos se retienen sin interpretar. Los segmentos
despiertos nunca cuentan como sueño; las fases solapadas generan
`sleep_overlap`. Cantidades aditivas solapadas generan `quantity_overlap`.
No se suman fases ni se mezclan productores.

La nueva exportación declara `kcal`, y este parser exige esa unidad exacta:
rechaza `cal`, `Cal` y otras unidades ambiguas para energía activa. El núcleo
histórico conserva su interpretación previa de `cal` como `kcal` para no
reinterpretar observaciones ya almacenadas. La corrección global de esa deuda
de unidades queda diferida a una migración o una ruta versionada.

## Identidad y límite de seguridad

La huella `derived-health-export:v1:…` es un hash de campos exportados,
incluidos intervalo y productor. Es estable frente al orden de claves y
offsets equivalentes, pero **no es un UUID de HealthKit**. Dos registros
físicos con los mismos campos exportados son indistinguibles. El parser
reporta `indistinguishable_records`. También detecta
`core_key_collision` cuando registros con huellas distintas caerían en
la misma clave actual de Health OS, que omite inicio, productor e
identificador de procedencia. `record_index` distingue filas dentro de
un mismo export, pero cambia al reordenarlas y tampoco es identidad de
muestra. Ninguno pasa a persistencia.

Antes de activar ingesta se necesita decidir la política de identidad,
verificar la atribución técnica a Apple Watch, granularidad,
solapamientos y cobertura por tipo. La preferencia fisiológica de
Apple Watch ya está decidida en la Política Operativa v0.5, sección
3.3. Esa política exige identificador de fuente, modelo y versión
de software; el parser marca `provenance_incomplete` si faltan. Una
fuente desconocida no puede mejorar Readiness. El export JSON real se
validó localmente solo mediante conteos; no se probó la transmisión HTTP
desde iPhone, ninguna escritura ni el Brief.

## Verificación

Las pruebas `tests/test_tuanhdo_dry_run.py` usan únicamente datos
ficticios del año 2000 con fuente `example.synthetic`. El validador
local, separado de las pruebas, leyó 9.753 registros de un JSON de
4.851.773 bytes y reconoció los 9.753: 5.289 de pulso, 1.939 de
energía activa, 1.294 de distancia, 1.041 de pasos, 188 de sueño y
2 de peso. Hay 2.754 cantidades puntuales (2.752 de pulso y 2 de
peso). Ninguna muestra es computable. El recibo señaló 201 filas con
procedencia incompleta: 188 de sueño, 10 de pulso, 2 de peso y 1 de
pasos; en todas faltaban modelo y versión de software. No hubo otros
códigos de incidencia en esta validación. El JSON y sus valores no se
incluyen en el repositorio ni en el parche.

## Integridad del informe auxiliar

`validate_health_export_local.py` acumula los motivos de retencion por
codigo. Si aparecen varios codigos desconocidos, sus cantidades se suman en
`other_hold` sin perder filas. La suma de `hold_reasons` debe coincidir con
`recognized_samples`. Una prueba sintetica cubre motivos distintos.
`issues` cuenta incidencias y puede diferir del numero de filas afectadas:
un solapamiento puede marcar dos o mas muestras.
El informe usa `name` cuando `type` es nulo, igual que el parser, y cuenta
`source_missing` por separado de paquete, modelo y version de software.
