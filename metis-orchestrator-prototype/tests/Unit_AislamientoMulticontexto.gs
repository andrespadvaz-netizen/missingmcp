/**
 * Unit_AislamientoMulticontexto.gs — aislamiento real entre contextos.
 *
 * Cubre las dos piezas que el aislamiento necesitaba y no tenía:
 *
 *   1. La clave de partición de Notion es fail closed. Una fila sin `Proyecto`
 *      o con un `Proyecto` que no mapea a ningún contexto declarado se rechaza,
 *      en vez de admitirse con contexto nulo en cualquier contexto.
 *
 *   2. El evaluador del smoke sabe exigir AUSENCIA, no sólo presencia. Sin
 *      canario negativo, una fuga que llegue con contexto nulo se cuenta como
 *      admitida, no como descartada, y el smoke la reporta como aislamiento
 *      correcto.
 *
 * Registro DIFERIDO, como el resto de los archivos de test: Apps Script
 * concatena los .gs en un orden que no controlamos, así que llamar a
 * `TestRunner` en tiempo de carga rompería el proyecto entero.
 */
function registerUnitAislamientoMulticontexto() {

  /** Fila mínima del registro de decisiones, con el `Proyecto` que se le pida. */
  function filaConProyecto(proyecto) {
    var props = {
      'Decision': { type: 'title', title: [{ plain_text: 'Barrido agosto — fila de prueba' }] },
      'Estado': { type: 'select', select: { name: 'vigente' } }
    };
    props['Proyecto'] = (proyecto === null)
      ? { type: 'select', select: null }
      : { type: 'select', select: { name: proyecto } };
    return {
      id: 'fila-de-prueba',
      url: 'https://app.notion.com/fila-de-prueba',
      parent: { data_source_id: '1436a1fc-159a-4e99-a6d6-0313f5932560' },
      properties: props
    };
  }

  var PARTICION_METIS = {
    data_sources: ['1436a1fc-159a-4e99-a6d6-0313f5932560'],
    project: 'Metis'
  };

  TestRunner.unit('Aislamiento', 'una fila sin Proyecto no entra en ningún contexto', function (t) {
    t.throwsCode(Errors.CODES.PARTITION_VIOLATION, function () {
      NotionReadAdapter.normalizePage(filaConProyecto(null), null, PARTICION_METIS);
    }, 'el `Proyecto` vacío se rechaza en vez de admitirse con contexto nulo');

    // Regresión del defecto exacto: la condición anterior era
    // `partition.project && project && project !== partition.project`, y con
    // `project` nulo salía por el segundo `&&` sin lanzar nunca.
    t.throwsCode(Errors.CODES.PARTITION_VIOLATION, function () {
      NotionReadAdapter.normalizePage(filaConProyecto(''), null, PARTICION_METIS);
    }, 'un `Proyecto` vacío como cadena tampoco pasa');
  });

  TestRunner.unit('Aislamiento', 'una fila de otro contexto no entra', function (t) {
    t.throwsCode(Errors.CODES.PARTITION_VIOLATION, function () {
      NotionReadAdapter.normalizePage(filaConProyecto('Andrea'), null, PARTICION_METIS);
    }, 'una fila de Andrea se rechaza desde una partición de Metis');

    t.throwsCode(Errors.CODES.PARTITION_VIOLATION, function () {
      NotionReadAdapter.normalizePage(filaConProyecto('Shokko'), null, PARTICION_METIS);
    }, 'una fila de Shokko también, aunque comparta modelo primario');
  });

  TestRunner.unit('Aislamiento', 'un Proyecto sin contexto declarado es deriva de esquema', function (t) {
    t.throwsCode(Errors.CODES.PARTITION_VIOLATION, function () {
      NotionReadAdapter.normalizePage(
        filaConProyecto('Proyecto Que No Existe'), null,
        { data_sources: PARTICION_METIS.data_sources, project: 'Proyecto Que No Existe' });
    }, 'un valor que no mapea a ningún contexto se rechaza en vez de admitirse con contexto nulo');
  });

  TestRunner.unit('Aislamiento', 'la fila propia sí entra y trae su contexto', function (t) {
    var doc = NotionReadAdapter.normalizePage(filaConProyecto('Metis'), null, PARTICION_METIS);
    t.equals(doc.context, 'METIS', 'la fila propia se admite con su contexto resuelto');
    t.equals(doc.source, 'NOTION', 'la fuente queda declarada');
    t.ok(doc.decision, 'se reconoce como fila del registro de decisiones');
  });

  TestRunner.unit('Aislamiento', 'el canario negativo detecta una fuga por identificador', function (t) {
    var admitidos = [
      { id: 'propio-1', title: 'Barrido agosto — Metis' },
      { id: 'ajeno-andrea', title: 'Barrido agosto — Andrea Plan Comercial Otoño' }
    ];
    var v = smokeEvaluateCanary({ forbid_id: 'ajeno-andrea' }, admitidos);
    t.equals(v.status, SMOKE_STATUS.FAIL, 'un objeto vetado admitido es FAIL');
    t.includes(v.detail, 'FUGA DE CONTEXTO', 'el detalle nombra la fuga sin ambigüedad');
  });

  TestRunner.unit('Aislamiento', 'el canario negativo detecta una fuga por título', function (t) {
    var admitidos = [{ id: 'x', title: 'Barrido agosto — Andrea Plan Comercial Otoño' }];
    var v = smokeEvaluateCanary({ forbid_title_contains: 'Andrea Plan Comercial' }, admitidos);
    t.equals(v.status, SMOKE_STATUS.FAIL, 'un título vetado admitido es FAIL');
  });

  TestRunner.unit('Aislamiento', 'una fuga no queda tapada por falta de resultados', function (t) {
    // Este es el caso que motivó evaluar los canarios negativos PRIMERO: con el
    // orden anterior, un solo admitido y `min_results: 5` devolvía
    // NO_DEMOSTRADO y la fuga nunca se reportaba.
    var admitidos = [{ id: 'ajeno-andrea', title: 'ajeno' }];
    var v = smokeEvaluateCanary({ forbid_id: 'ajeno-andrea', min_results: 5 }, admitidos);
    t.equals(v.status, SMOKE_STATUS.FAIL, 'la fuga gana sobre el NO_DEMOSTRADO');
  });

  TestRunner.unit('Aislamiento', 'la cardinalidad exacta distingue fuga de sobre-filtrado', function (t) {
    var tres = [{ id: 'a', title: 'a' }, { id: 'b', title: 'b' }, { id: 'c', title: 'c' }];
    var demas = smokeEvaluateCanary({ expect_exactly: 2 }, tres);
    t.equals(demas.status, SMOKE_STATUS.FAIL, 'tres donde se esperaban dos es FAIL');
    t.includes(demas.detail, 'De más', 'se nombra como fuga, no como error genérico');

    var demenos = smokeEvaluateCanary({ expect_exactly: 2 }, [{ id: 'a', title: 'a' }]);
    t.equals(demenos.status, SMOKE_STATUS.FAIL, 'uno donde se esperaban dos también es FAIL');
    t.includes(demenos.detail, 'De menos', 'se nombra como sobre-filtrado');
  });

  TestRunner.unit('Aislamiento', 'la corrida limpia pasa con los tres criterios', function (t) {
    var admitidos = [
      { id: 'metis-1', title: 'Barrido agosto — cierre de tres pendientes' },
      { id: 'metis-2', title: 'Barrido agosto — A2 priorizado' }
    ];
    var v = smokeEvaluateCanary({
      expect_id: 'metis-1',
      expect_exactly: 2,
      forbid_id: '3c4df4e9-5ef0-8171-9f9c-f09cb9c10ff2',
      forbid_title_contains: 'Andrea Plan Comercial'
    }, admitidos);
    t.equals(v.status, SMOKE_STATUS.PASS, 'presencia, cardinalidad y ausencia se cumplen a la vez');
    t.includes(v.detail, 'canario negativo', 'el detalle deja constancia de la ausencia comprobada');
  });

  TestRunner.unit('Aislamiento', 'el ensayo declarado usa dos contextos y veta a Andrea', function (t) {
    // Guarda contra una regresión silenciosa del propio ensayo: si alguien lo
    // reduce a un solo contexto o le quita el canario negativo, deja de probar
    // aislamiento aunque siga dando PASS.
    var texto = String(smokeAislamientoMetisShokko);
    t.includes(texto, "'METIS', 'SHOKKO'", 'el ensayo declara los dos contextos');
    t.includes(texto, 'ANDREA_ID_VETADO', 'el ensayo mantiene el canario negativo de Andrea');
    t.includes(texto, 'expect_exactly', 'el ensayo mantiene la comprobación de cardinalidad');
    t.notOk(/contexts:\s*\['ANDREA'/.test(texto), 'Andrea nunca se declara como contexto a leer');
  });

}
