# Health OS source of truth matrix

| Category | Preferred source | Notes |
| --- | --- | --- |
| Raw Apple Health transport | Authorized Apple Health adapter | Preserve raw sample ID; ChatGPT narrative is not a substitute. |
| Apple Health consultation | ChatGPT Health | Query/explanation surface; summaries are `model_interpreted`. |
| Heart rate, resting heart rate, HRV, sleep, respiratory rate | Apple Health | Preserve sample source, time, unit, and semantics. |
| Steps, distance, active energy, physiological workout response | Apple Health | Do not infer strength detail from these records. |
| Body mass and composition | Authorized Apple Health body source | Record the originating device/app. |
| Exercise / workout duration | Apple Health | Fuente operativa actual de sesiones; conserva procedencia y no inventa detalle de fuerza. |
| Series, repeticiones, resistencia y volumen | Speediance, si ofrece una exportación documentada | Diferido: Speediance no publica hoy un CSV/JSON/API de exportación confirmado para Gym Monster. Nunca se manufacturan campos ausentes. |
| Subjective state and symptoms | Operator | Only inputs that cannot be sensed; each needs a decision purpose. |
| Medication context | Current authorized structured profile | Context only; never a treatment instruction. |
| Interpretation | Health OS | Conservative and non-clinical. |
| Garmin | Legacy history only | Readable but never a forward runtime source. |
