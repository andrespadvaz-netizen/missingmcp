# Health OS: contrato del Brief diario

La sección Health OS presenta sólo una decisión conservadora y la evidencia que
la sustenta. No replica el almacén de datos ni realiza diagnóstico.

## Orden y reglas de emisión

1. **Integridad:** mostrar última sincronización, conflictos o faltantes; una
   línea cuando todo esté sano.
2. **Recuperación:** sueño, FC reposo y HRV únicamente si tienen cobertura
   suficiente para la métrica concreta.
3. **Carga y fuerza:** actividad, entrenamiento y sesión lógica Apple/
   Speediance, sin doble conteo.
4. **Cambio relevante:** composición, movilidad, nutrición histórica, ruido o
   mindfulness sólo si cambia la decisión.
5. **Estado:** `INFORMATIVO`, `REVISAR_TECNICO` o `ABSTENERSE`, con
   factores, confianza, limitación y dato faltante.

El Brief se abstiene si la sincronización está atrasada, hay conflicto de
fuentes, falta baseline de sueño/FC reposo o sólo existe información
`model_interpreted`. Las observaciones clínicas sensibles se describen como
hechos de fuente y se escalan; nunca reciben interpretación diagnóstica.

La implementación expone este contrato como `POST /health-os/brief`, protegido
por la clave dedicada de lectura. Esta es la única fuente técnica del bloque
Health del Brief diario: el protocolo en Notion debe consumir este snapshot y no
una segunda lógica ni narrativa de ChatGPT. Su respuesta no contiene mediciones
crudas.
