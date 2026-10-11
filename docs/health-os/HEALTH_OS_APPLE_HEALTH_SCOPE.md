# Health OS: alcance Apple Health / ChatGPT Health

Fecha de evidencia: 2026-10-11. Inventario verificado por la conexión Health
nativa de ChatGPT: 91 tipos admitidos; 53 con registros sincronizados. Este
documento reemplaza el alcance reducido de “actividad, sueño y fuerza”.

## Principio operativo

Health OS no intenta producir una puntuación médica ni copiar todo Apple
Health. Convierte la evidencia disponible en tres cosas: **estado**,
**tendencia** y **decisiones revisables**. Cada salida declara fuente, período,
cobertura y nivel de confianza. Correlación nunca se presenta como causalidad.

## Dominios y decisiones

| Dominio | Métricas disponibles | Pregunta o decisión que habilita |
|---|---|---|
| Recuperación cardiopulmonar | FC, FC reposo, HRV/SDNN, FC caminando, SpO2, respiración, presión arterial, ECG | Ajustar la exigencia de entrenamiento; distinguir señal reciente de cambio robusto; elevar una observación para revisión clínica, sin diagnosticar. |
| Sueño y regulación | análisis de sueño, atención plena | Proteger recuperación, detectar inconsistencia horaria y evaluar asociaciones con carga y señales fisiológicas. |
| Carga y movimiento | pasos, distancias, pisos, energía activa/basal, ejercicio, tiempo de pie, entrenamientos, bici | Elegir entre entrenar, recuperar, caminar o mantener; observar carga semanal y adherencia. |
| Fuerza | entrenamientos Apple + detalle Speediance | Planificar progresión, volumen y recuperación muscular; evitar doble conteo de una misma sesión. |
| Composición corporal | peso, grasa, masa magra, IMC, estatura | Evaluar cambios lentos de composición, no reaccionar a fluctuaciones aisladas de peso. |
| Nutrición histórica | energía, macros, grasa saturada, fibra, azúcar, colesterol, sodio y micronutrientes | Detectar patrones y huecos históricos; no usar como ingesta actual hasta que se reanude el registro. |
| Movilidad | velocidad, longitud de paso, asimetría, doble apoyo, velocidad en escaleras | Vigilar cambios sostenidos y decidir si se reduce impacto o se revisa el contexto. No diagnostica lesiones. |
| Entorno y hábitos | ruido ambiental/auriculares, mindfulness | Vincular exposición/hábitos con sueño y recuperación; proponer higiene del entorno. |

## Ventanas de evidencia

Cada métrica se clasifica antes de influir en una decisión:

- **Histórica útil:** series desde 2019–2023, aptas para línea de tendencia.
- **Baseline Apple reciente:** señales iniciadas en 2026, como HRV, SpO2,
  respiración y escaleras. Se muestran, pero no sostienen conclusiones fuertes
  hasta reunir 14 días distintos y una cobertura suficiente.
- **Histórica interrumpida:** nutrición disponible principalmente entre mayo
  de 2023 y noviembre de 2025; sirve para patrones previos, no para afirmar la
  dieta de hoy.
- **No disponible:** se representa como ausencia, nunca como cero ni como
  recomendación negativa.

## Brief diario: contrato propuesto

El Brief recibe una sección `HEALTH OS` de máximo cinco decisiones, no una
tabla interminable:

1. **Integridad:** última sincronización, dominios presentes/ausentes y
   confianza de cada afirmación.
2. **Recuperación:** sueño + FC reposo + HRV + respiración/SpO2 cuando exista
   línea de referencia suficiente.
3. **Carga:** actividad de ayer, entrenamiento, energía y detalle Speediance.
4. **Cuerpo y hábito:** sólo cambios relevantes de composición, movilidad,
   nutrición histórica o ruido; no repetir métricas estables.
5. **Decisión del día:** una propuesta condicional: carga normal, carga
   reducida, recuperación activa, revisar registro o solicitar evaluación
   profesional si una señal clínica persiste.

Formato ejemplo:

```text
HEALTH OS | evidencia hasta 10 oct, 08:00
Recuperación: sueño disponible; HRV tiene 7 días, se muestra sin tendencia robusta.
Carga: sesión de fuerza enlazada Apple Health + Speediance; sin doble conteo.
Cuerpo: sin cambio semanal suficientemente grande en composición.
Decisión: mantener la sesión planificada sólo si la próxima lectura conserva
la cobertura; de lo contrario, recuperación activa. Confianza: media.
```

## Vistas de consulta en ChatGPT

- **Ahora:** “¿Qué evidencia tengo hoy para decidir mi entrenamiento?”
- **Semana:** “Compara sueño, HRV, FC reposo, carga y fuerza contra las cuatro
  semanas anteriores; separa hechos, tendencias e incertidumbre.”
- **Composición:** “Analiza 12 semanas de peso, grasa y masa magra; ignora
  cambios de un día.”
- **Movilidad:** “Busca cambios sostenidos de marcha y escaleras; no hagas
  diagnóstico.”
- **Nutrición:** “Resume únicamente el periodo con registro y señala que no
  representa el consumo actual si no hay datos nuevos.”
- **Calidad:** “Enumera métricas recientes, antiguas, ausentes y conflictivas.”

## Límites innegociables

- ChatGPT Health es el conector primario de consulta; no presupone una copia
  programática automática de todo el iPhone hacia Metis.
- Garmin propietario (Body Battery, Training Readiness, Training Load y
  estrés) no se reconstruye ni se reactiva.
- No se usan características personales estáticas ni ECG para decisiones de
  entrenamiento automatizadas; sólo se consultan cuando el usuario lo pide y
  con lenguaje no diagnóstico.
- Alertas de presión arterial, ECG, oxigenación o ritmo se describen como
  observaciones para revisión profesional, no como diagnóstico o tratamiento.
