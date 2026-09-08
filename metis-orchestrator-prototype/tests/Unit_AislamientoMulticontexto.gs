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

  /**
   * Fila del registro de decisiones con TODAS las propiedades estructurales
   * presentes y el `Proyecto` que se le pida. Debe estar completa: una fila a
   * la que le falte una propiedad ya no prueba partición, prueba deriva de
   * esquema, que es otra cosa y se prueba aparte.
   */
  function filaConProyecto(proyecto) {
    var props = {
      'Decision': { type: 'title', title: [{ plain_text: 'Barrido agosto — fila de prueba' }] },
      'Estado': { type: 'select', select: { name: 'vigente' } },
      'Sustituida por': { type: 'relation', relation: [] },
      'Sustituye a': { type: 'relation', relation: [] }
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

  /** La misma fila, pero sin la propiedad que se le indique. */
  function filaSinPropiedad(nombre) {
    var fila = filaConProyecto('Metis');
    delete fila.properties[nombre];
    return fila;
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

  // ---------------------------------------------------- deriva de esquema

  TestRunner.unit('Deriva de esquema', 'borrar Estado no degrada en silencio', function (t) {
    // El caso más peligroso: sin la columna `Estado`, todas las filas caerían
    // en la regla de vigencia por defecto y el prototipo afirmaría vigencia
    // sobre decisiones que nadie declaró vigentes, sin un solo error.
    t.throwsCode(Errors.CODES.SCHEMA, function () {
      NotionReadAdapter.normalizePage(filaSinPropiedad('Estado'), null, PARTICION_METIS);
    }, 'la ausencia de la columna Estado lanza en vez de leerse como valor vacío');
  });

  TestRunner.unit('Deriva de esquema', 'borrar una relación de sustitución falla', function (t) {
    // Sin las relaciones, la cadena de vigencia no se puede cerrar, y el
    // prototipo creería haberla cerrado porque no encuentra eslabones.
    t.throwsCode(Errors.CODES.SCHEMA, function () {
      NotionReadAdapter.normalizePage(filaSinPropiedad('Sustituida por'), null, PARTICION_METIS);
    }, 'sin `Sustituida por` la lectura falla');
    t.throwsCode(Errors.CODES.SCHEMA, function () {
      NotionReadAdapter.normalizePage(filaSinPropiedad('Sustituye a'), null, PARTICION_METIS);
    }, 'sin `Sustituye a` también');
  });

  TestRunner.unit('Deriva de esquema', 'renombrar Proyecto no se confunde con valor vacío', function (t) {
    // Distinción que da sentido a toda la guarda: una fila SIN la propiedad es
    // un esquema distinto; una fila CON la propiedad vacía es un dato. Los dos
    // se rechazan, pero por motivos distintos y con códigos distintos.
    t.throwsCode(Errors.CODES.SCHEMA, function () {
      NotionReadAdapter.normalizePage(filaSinPropiedad('Proyecto'), null, PARTICION_METIS);
    }, 'la columna ausente es deriva de esquema');
    t.throwsCode(Errors.CODES.PARTITION_VIOLATION, function () {
      NotionReadAdapter.normalizePage(filaConProyecto(null), null, PARTICION_METIS);
    }, 'la columna presente y vacía es violación de partición');
  });

  TestRunner.unit('Deriva de esquema', 'cambiar Estado de select a texto falla', function (t) {
    // El caso que la primera versión de la guarda dejaba pasar: la clave sigue
    // existiendo, así que la comprobación de presencia daba PASS, y luego
    // `_selectOf` devolvía null por no ser select. Resultado: todas las filas
    // con el tipo cambiado se leían como "Estado vacío legítimo" y caían en la
    // regla de vigencia por defecto, sin un solo error.
    var fila = filaConProyecto('Metis');
    fila.properties['Estado'] = {
      type: 'rich_text', rich_text: [{ plain_text: 'vigente' }]
    };
    t.throwsCode(Errors.CODES.SCHEMA, function () {
      NotionReadAdapter.normalizePage(fila, null, PARTICION_METIS);
    }, 'un cambio de tipo en Estado lanza en vez de leerse como vacío');
  });

  TestRunner.unit('Deriva de esquema', 'cambiar una relación de sustitución a texto falla', function (t) {
    // Más grave que el anterior: `_relationIds` devuelve lista vacía cuando el
    // tipo no es relation, así que una cadena real de sustitución desaparece
    // del modelo y puede producir una decisión vigente FALSA.
    var fila = filaConProyecto('Metis');
    fila.properties['Sustituida por'] = {
      type: 'rich_text', rich_text: [{ plain_text: 'alguna-pagina' }]
    };
    t.throwsCode(Errors.CODES.SCHEMA, function () {
      NotionReadAdapter.normalizePage(fila, null, PARTICION_METIS);
    }, 'una relación convertida en texto lanza en vez de leerse como cadena vacía');

    var otra = filaConProyecto('Metis');
    otra.properties['Sustituye a'] = { type: 'relation', relation: null };
    t.throwsCode(Errors.CODES.SCHEMA, function () {
      NotionReadAdapter.normalizePage(otra, null, PARTICION_METIS);
    }, 'una relación que no es lista tampoco pasa');
  });

  TestRunner.unit('Deriva de esquema', 'un valor de Estado fuera del dominio falla', function (t) {
    // Deriva de DOMINIO, no de esquema: la columna existe y es del tipo
    // correcto, pero el valor es nuevo. `toDecision` lo mapea a null y produce
    // la misma falsedad que un Estado ausente.
    var fila = filaConProyecto('Metis');
    fila.properties['Estado'] = { type: 'select', select: { name: 'supersedida' } };
    t.throwsCode(Errors.CODES.SCHEMA, function () {
      NotionReadAdapter.normalizePage(fila, null, PARTICION_METIS);
    }, 'un valor nuevo en el select lanza en vez de convertirse en null');
  });

  TestRunner.unit('Deriva de esquema', 'los tres valores del dominio sí pasan', function (t) {
    var estados = ['vigente', 'modificada', 'derogada'];
    for (var i = 0; i < estados.length; i++) {
      var fila = filaConProyecto('Metis');
      fila.properties['Estado'] = { type: 'select', select: { name: estados[i] } };
      var doc = NotionReadAdapter.normalizePage(fila, null, PARTICION_METIS);
      t.equals(doc.decision.estado, estados[i], estados[i] + ' es un valor válido');
    }
  });

  TestRunner.unit('Deriva de esquema', 'la comprobación es por clave, no por valor', function (t) {
    var fila = filaConProyecto('Metis');
    fila.properties['Estado'] = { type: 'select', select: null };
    var doc = NotionReadAdapter.normalizePage(fila, null, PARTICION_METIS);
    t.equals(doc.decision.estado, null, 'un Estado vacío es dato legítimo y se conserva como null');
    t.equals(doc.context, 'METIS', 'la fila se admite con su contexto');
  });

  // -------------------------------------------- pertenencia ajena en Asana

  /** Tarea de Asana con la lista de proyectos que se le indique. */
  function tareaEnProyectos(gids) {
    var projects = [];
    for (var i = 0; i < gids.length; i++) { projects.push({ gid: gids[i], name: 'p' + i }); }
    return { gid: '999', name: 'tarea de prueba', projects: projects };
  }

  TestRunner.unit('Aislamiento', 'los proyectos ajenos se calculan desde las particiones declaradas', function (t) {
    Config._setPartitions({
      METIS:  { asana: { project_gids: ['100'] } },
      SHOKKO: { asana: { project_gids: ['200', '201'] } }
    });
    try {
      var ajenos = AsanaReadAdapter.foreignProjectGids('METIS');
      t.equals(ajenos['200'], 'SHOKKO', 'un proyecto de Shokko es ajeno para Metis');
      t.equals(ajenos['201'], 'SHOKKO', 'y el segundo también');
      t.notOk(ajenos['100'], 'el propio no figura como ajeno');
    } finally {
      // Restaurar las particiones del entorno de test, no dejarlas en null:
      // null significa "leer de Script Properties" y contaminaría a cualquier
      // prueba posterior que dependa del corpus de fixtures.
      Config._setPartitions(Fixtures.PARTITIONS);
    }
  });

  TestRunner.unit('Aislamiento', 'una tarea en proyectos de contextos incompatibles se rechaza', function (t) {
    Config._setPartitions({
      METIS:  { asana: { project_gids: ['100'] } },
      SHOKKO: { asana: { project_gids: ['200'] } }
    });
    try {
      // Caso adversarial: la tarea SÍ está en un proyecto permitido de Metis.
      // La versión anterior se daba por satisfecha con eso y la etiquetaba
      // METIS. Ahora la pertenencia simultánea a un proyecto de Shokko la
      // descalifica: no se resuelve a favor del contexto de la corrida.
      t.throwsCode(Errors.CODES.PARTITION_VIOLATION, function () {
        AsanaReadAdapter.assertNoForeignMembership(
          tareaEnProyectos(['100', '200']), { context: 'METIS' });
      }, 'la tarea puente se rechaza aunque uno de sus proyectos sea propio');

      t.ok(AsanaReadAdapter.assertNoForeignMembership(
        tareaEnProyectos(['100']), { context: 'METIS' }),
        'una tarea sólo en proyectos propios sí pasa');

      t.ok(AsanaReadAdapter.assertNoForeignMembership(
        tareaEnProyectos(['100', '300']), { context: 'METIS' }),
        'un proyecto no declarado en ninguna partición no la descalifica');
    } finally {
      // Restaurar las particiones del entorno de test, no dejarlas en null:
      // null significa "leer de Script Properties" y contaminaría a cualquier
      // prueba posterior que dependa del corpus de fixtures.
      Config._setPartitions(Fixtures.PARTITIONS);
    }
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
