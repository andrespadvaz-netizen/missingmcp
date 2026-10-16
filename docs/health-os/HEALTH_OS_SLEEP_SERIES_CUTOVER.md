# Serie nueva de sue├▒o: corte aprobado y preparaci├│n local

Andr├®s aprob├│ iniciar una serie separada el **16 de octubre de 2026 a las
00:00:00, UTCÔêÆ06:00 (Ciudad de M├®xico)**. El c├│digo de este checkout es una
preparaci├│n local; a├║n requiere auditor├¡a independiente antes de cualquier
despliegue o cambio de configuraci├│n productiva.

## Estado comprobado antes del corte

En los cinco lotes de staging hay 738 filas de `sleep_duration`, que representan
180 huellas distintas. Una clasificaci├│n de solo lectura de sus intervalos
cifrados confirm├│ que **las 738 terminan antes del corte**. No hay retenidos
posteriores al corte que revisar o promover hoy. El ├║ltimo lote tiene 180 filas
de sue├▒o, tambi├®n todas anteriores. El core conserva 7.554 observaciones
hist├│ricas de sue├▒o sin inicio ni fin expl├¡cito. No se borran ni se reetiquetan.

## Cambio local

- `HEALTH_OS_SLEEP_SERIES_MODE=active` activa el corte exacto fijado en c├│digo.
  El valor predeterminado `off` mantiene el comportamiento previo hasta el
  despliegue autorizado. `paused` conserva el aislamiento y la lectura de la
  serie nueva, pero retiene toda nueva promoci├│n de sue├▒o.
- Se aceptan solo segmentos de sue├▒o con inicio y fin v├ílidos, inicio en el
  corte o despu├®s, procedencia Watch aprobada y tipo aprobado. Los que cruzan
  el corte se retienen completos; no se truncan. Los controles de duplicado,
  colisi├│n y solapamiento entre segmentos nuevos siguen activos.
- El core de sue├▒o actual usa exclusivamente observaciones enlazadas a una
  promoci├│n de la serie nueva cuyo intervalo cifrado se comprueba de nuevo al
  leer. Cobertura, baseline, tendencias, calidad y decisi├│n comparten ese
  filtro. La historia mensual conserva legacy y nuevo en filas `series`
  separadas. Las otras m├®tricas no cambian.
- La migraci├│n de esquema es solo una tabla de auditor├¡a aditiva,
  `health_os_sleep_series_audit`; no copia ni cambia observaciones. **Al primer
  arranque `active`, antes de aceptar sue├▒o**, una transacci├│n registra el
  corte all├¡ y una marca cifrada en `health_os_state`. La reversi├│n de
  promociones no elimina ninguna de las dos. Si falta o difiere una traza,
  todos los modos fallan cerrados. Tras la activaci├│n, `off` o la ausencia de
  la variable impiden arrancar aunque se hayan revertido todos los registros;
  `paused` mantiene el aislamiento. Las instancias de esta versi├│n tambi├®n
  revisan las trazas en cada lectura y operaci├│n de ingesta, por lo que una
  instancia `off` ya iniciada falla al detectar la activaci├│n externa.

## Activaci├│n y reversi├│n, pendientes de revisi├│n

1. Revisar diff, pruebas, contrato de historia y los controles de privacidad.
   Registrar respaldo recuperable y versi├│n exacta del servicio. Confirmar
   que el servicio Railway usa una sola r├®plica y conocer el procedimiento
   verificable para drenar todas las instancias de una revisi├│n anterior.
2. Desplegar esta versi├│n primero con `HEALTH_OS_SLEEP_SERIES_MODE=off` y
   comprobar que **todas** las instancias antiguas salieron y que cada
   instancia activa anuncia el commit esperado. Durante este paso a├║n no se
   activa el corte. Si no puede probarse el drenaje, detener la activaci├│n.
3. Bloquear temporalmente nuevas solicitudes al ingreso, esperar a que terminen
   las solicitudes en curso y comprobar cero instancias/solicitudes de la
   revisi├│n `off`. Solo entonces activar `HEALTH_OS_SLEEP_SERIES_MODE=active`
   en el servicio exacto y levantar una sola instancia de la misma versi├│n.
   Comprobar commit, dos trazas de activaci├│n, una ├║nica instancia activa y
   cobertura de sue├▒o 0/28 antes de reabrir el ingreso. El `railway.json`
   actual no demuestra por s├¡ solo el n├║mero de r├®plicas ni el drenaje; son
   verificaciones de despliegue obligatorias, no supuestos.
4. Tras un export posterior al corte, verificar solo recibo por motivos,
   enlaces, cobertura y ausencia de solapamiento. No revisar ni promover los
   738 registros previos al corte. Si un post-corte qued├│ previamente retenido,
   usar revisi├│n expl├¡cita de **ese lote** con `approved_types={sleepAnalysis}`
   y la procedencia ya autorizada. No ampliar tipos ni fuentes.
5. Para detener el cambio sin p├®rdida, pasar a `paused`: no entran nuevos
   registros de sue├▒o; los ya aceptados y el historial siguen cifrados e
   intactos, y el filtro evita mezcla. Se puede volver a `active` tras corregir
   el problema. La activaci├│n inicial fija de forma duradera el aislamiento,
   incluso con cero aceptados. No volver a `off` ni desplegar c├│digo anterior que ignore el
   filtro despu├®s de activar el corte. Una reversi├│n total necesitar├¡a una
   operaci├│n separada, acotada y auditada sobre los enlaces nuevos; no est├í
   autorizada ni incluida aqu├¡.

Las pruebas sint├®ticas cubren corte, procedencia, solapamiento, duplicados,
colisiones, lectura de la serie, conservaci├│n de historia, pausa y rechazo de
un enlace inv├ílido. Usan SQLite real temporal. Incluyen activaci├│n sin
aceptados y reversi├│n de **todos** los aceptados; en ambos casos, un reinicio
con modo `off` o variable ausente falla cerrado y `paused` conserva el filtro.
Una marca da├▒ada o borrada tambi├®n impide el arranque por la traza de auditor├¡a.
La prueba de dos repositorios verifica que una instancia `off` ya creada deja
de servir lecturas y escrituras al activarse otra. Sigue pendiente confirmar
en producci├│n que un nuevo
export **posterior** al corte efectivamente llega; esta preparaci├│n no lo env├¡a.
