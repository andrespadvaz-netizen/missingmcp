# Validación de versión desplegada

Entrada autorizada el 6 de septiembre de 2026 sobre el baseline
`919de8fb2c935cb16c7578ea267bc1a57f74d0ce`.

Sólo proyecto corporativo `1g9BKx5-TShUuUi2w0fZLByFydG8_l1WB74vn-MDOYWc4nd3OGiolDp9o`,
desplegado por `andrespv@venturequest.mx`. La copia personal no se modifica.

El manifiesto restringe la aplicación web a `MYSELF`, ejecutada como
`USER_DEPLOYING`. Los tres permisos OAuth existentes permanecen idénticos.
GET sin parámetros ejecuta exclusivamente `TestRunner.runAll()` dentro de
aislamiento de servicios y propiedades en memoria. POST, rutas, parámetros
y ejecución sin evento se rechazan. No hay HTML ni puente `google.script.run`.
Las simulaciones de transporte sustituyen al servicio ya bloqueado; nunca
reciben el servicio de red real. El resultado excluye errores y respuestas
crudas. Las pruebas nuevas se ejecutan también dentro de la suite desplegada.

## Evidencia requerida

1. Crear commit y versión inmutable; desplegar esa versión con acceso MYSELF.
2. Consultar el deployment externamente y registrar su número de versión.
3. Descargar `projects.getContent` con ese `versionNumber` a carpeta vacía.
4. Comparar inventario completo y contenido contra el commit, normalizando
   únicamente nombres/extensiones de Apps Script y CRLF a LF. Ningún archivo
   extra ni diferencia de espacios/comentarios es aceptable.
5. Visitar la URL `/exec` sin parámetros con la cuenta corporativa. Guardar
   el informe junto con deployment, versión, commit y comparación externa.

La URL devuelta por el runtime sólo localiza el deployment. No acredita
identidad. El número de versión se obtiene del registro externo del deployment;
no se acepta un número suministrado por el solicitante ni un hash en constante.
La URL `/dev` y el botón del editor no acreditan una versión fijada.

## Pruebas locales

`node tools/run_local.js all`

`node tools/run_local.js validation`

`node tools/static_guards.js`

Las pruebas locales no acreditan el runtime de Google. Antes de los cuatro
gaps funcionales siguientes debe completarse la evidencia desplegada. Si
Google exige consentimiento adicional, se detiene esa ruta y se registra el
criterio como pendiente. No se invocan proveedores reales ni LEVEL_3.
