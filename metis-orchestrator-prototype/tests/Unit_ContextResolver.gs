/**
 * Unit_ContextResolver.gs — spec §17 / ContextResolver.
 *
 * Nota de ubicación: la spec §3 fija la lista de archivos de test, así que las
 * pruebas de PARTICIÓN DE FUENTES por contexto y de VIGENCIA del registro de
 * decisiones viven al final de este archivo — ambas responden a la misma
 * pregunta: qué puede ver una corrida de un contexto, y qué puede afirmar.
 */
/**
 * Registro DIFERIDO: las pruebas de ContextResolver.
 *
 * NO es un IIFE. Apps Script concatena los .gs en un orden que no
 * controlamos, así que llamar a `TestRunner` en tiempo de carga rompe el
 * proyecto entero cuando este archivo se evalúa antes que TestRunner.gs
 * (una declaración `function` sí se hoistea; `var TestRunner = (...)()` no).
 * `TestRunner` invoca esta función desde los runners, ya con todo cargado.
 */
function registerUnitContextResolver() {

  TestRunner.unit('ContextResolver', 'identifica contexto único', function (t) {
    var verdict = ContextResolver.resolve('¿Cuál es el estado del gate de Ciclo 1 a Ciclo 2 en Metis?');
    t.deepEquals(verdict.candidates, ['METIS'], 'un solo candidato detectado');
    t.equals(verdict.resolved_context, 'METIS', 'contexto resuelto a METIS');
    t.equals(verdict.status, 'CONTEXT_RESOLVED', 'estado CONTEXT_RESOLVED');
    t.ok(verdict.scope.substance_allowed, 'con contexto resuelto se permite sustancia');
  });

  TestRunner.unit('ContextResolver', 'detecta conflicto multi-contexto', function (t) {
    var verdict = ContextResolver.resolve('Compara el pipeline de Shokko con la reorganización de Andrea');
    t.equals(verdict.candidates.length, 2, 'dos candidatos materiales');
    t.ok(ContextResolver.hasIncompatiblePair(verdict.candidates), 'los candidatos son incompatibles');
    t.equals(verdict.resolved_context, null, 'no resuelve contexto');
    t.equals(verdict.route, 'REQUIRES_ANDRES', 'bloquea en REQUIRES_ANDRES');
    t.equals(verdict.reason, 'CONTEXT_AMBIGUOUS', 'motivo explícito');
  });

  TestRunner.unit('ContextResolver', 'no mezcla sustancia antes de resolver', function (t) {
    var verdict = ContextResolver.resolve('Shokko y Andrea a la vez');
    t.notOk(verdict.scope.substance_allowed, 'el alcance no permite sustancia');
    t.throwsCode(Errors.CODES.CONTEXT_AMBIGUOUS, function () {
      ContextResolver.assertReadAllowed(verdict.scope, 'ANDREA', true);
    }, 'la lectura de sustancia se rechaza con contexto sin resolver');
    t.ok(ContextResolver.assertReadAllowed(verdict.scope, 'ANDREA', false),
      'la lectura de metadatos dentro de los candidatos sí se permite');
    t.throwsCode(Errors.CODES.AUTHORITY_DENIED, function () {
      ContextResolver.assertReadAllowed(verdict.scope, 'METIS', false);
    }, 'no se lee fuera de los contextos candidatos');
  });

  TestRunner.unit('ContextResolver', 'la instrucción del operador desambigua sin recuperar', function (t) {
    var verdict = ContextResolver.resolve('Shokko y Andrea a la vez', { operator_context: 'SHOKKO' });
    t.equals(verdict.resolved_context, 'SHOKKO', 'el turno vivo del operador resuelve el contexto');
    t.deepEquals(verdict.candidates, ['SHOKKO'], 'los candidatos se reducen al declarado');
  });

  TestRunner.unit('ContextResolver', 'clasifica la intención de la petición', function (t) {
    t.equals(ContextResolver.detectIntent('Audita esta propuesta de gobernanza'), 'audit', 'auditoría');
    t.equals(ContextResolver.detectIntent('Actualiza la tarea de Asana'), 'execution', 'ejecución');
    t.equals(ContextResolver.detectIntent('¿Cuál es el estado del gate?'), 'retrieval', 'recuperación');
  });


  // ------------------------------------------- partición efectiva de fuentes
  function sessionFor(context) {
    var scope = ContextResolver.scopeFor([context], context);
    var grant = AuthorityPolicy.preRetrievalGrant([context]);
    return ToolBroker.newSession({ execution_id: 'exec-part' }, scope, grant);
  }

  TestRunner.unit('Partición', 'sin partición declarada no se lee la fuente', function (t) {
    var session = sessionFor('VENTURE_QUEST'); // contexto sin partición en fixtures
    t.equals(Config.partitionFor('VENTURE_QUEST', 'NOTION'), null, 'no hay partición declarada');
    t.throwsCode(Errors.CODES.SOURCE_PARTITION_UNDECLARED, function () {
      ToolBroker.invoke(session, 'notion.search', { query: 'lo que sea' });
    }, 'la lectura se rechaza: fail closed, no "lee todo y filtra"');
  });

  TestRunner.unit('Partición', 'el contenido de otro contexto es INALCANZABLE, no filtrado', function (t) {
    var session = sessionFor('METIS');

    // `and-01` existe en el corpus, pero vive en el contenedor de ANDREA.
    var busqueda = ToolBroker.invoke(session, 'notion.search', { query: 'reorganizacion' });
    t.equals(busqueda.documents.length, 0, 'una búsqueda desde METIS no ve documentos de ANDREA');
    t.equals(session.risk_signals.length, 0,
      'y no hay señal de "descartado": nunca llegó a la sesión');

    // Pedirlo por id tampoco funciona, y falla RUIDOSAMENTE: una violación de
    // partición no se degrada a "fuente sin cobertura", que ocultaría el intento.
    t.throwsCode(Errors.CODES.PARTITION_VIOLATION, function () {
      ToolBroker.invoke(session, 'notion.fetch', { id: 'and-01' });
    }, 'la lectura directa de un objeto ajeno lanza violación de partición');
    t.notOk(session.source_status.NOTION && session.source_status.NOTION.covered === false,
      'y no marca la fuente como sin cobertura: el problema no es la fuente');

    // Lo propio del contexto sí se alcanza.
    var propio = ToolBroker.invoke(session, 'notion.search', { query: 'gate' });
    t.equals(propio.documents.length, 1, 'lo que sí está en la partición se lee');
    t.equals(propio.documents[0].id, 'met-gate-01', 'y es el documento del contexto resuelto');
  });

  TestRunner.unit('Partición', 'ninguna lectura ocurre sin contexto resuelto', function (t) {
    var scope = ContextResolver.scopeFor(['METIS', 'ANDREA'], null);
    var session = ToolBroker.newSession({ execution_id: 'exec-amb' }, scope,
      AuthorityPolicy.preRetrievalGrant(['METIS', 'ANDREA']));
    t.throwsCode(Errors.CODES.CONTEXT_AMBIGUOUS, function () {
      ToolBroker.invoke(session, 'notion.search', { query: 'gate' });
    }, 'con dos candidatos no se toca ninguna fuente');
  });

  TestRunner.unit('Partición', 'el contexto del resultado es el resuelto, no el nombre del proyecto', function (t) {
    var session = sessionFor('METIS');

    // El proyecto real se llama "Metis — Sistema Operativo": no coincide con la
    // clave de contexto `METIS`. Derivar el contexto de ese nombre producía
    // "METIS___SISTEMA_OPERATIVO" y el filtro de contaminación descartaba la
    // tarea en silencio.
    var doc = Fixtures.CORPUS.filter(function (d) { return d.id === 'met-task-12'; })[0];
    t.equals(doc.project_name, 'Metis — Sistema Operativo', 'el fixture usa el nombre real del proyecto');
    t.notOk(ContextResolver.normalize(doc.project_name).toUpperCase().replace(/[^A-Z_]/g, '_') === 'METIS',
      'derivar el contexto de ese nombre NO da METIS');

    var result = ToolBroker.invoke(session, 'asana.search', { query: 'registrar' });
    t.equals(result.documents.length, 1, 'la tarea se devuelve');
    t.equals(result.documents[0].id, 'met-task-12', 'y es la esperada');
    t.equals(result.documents[0].context, 'METIS', 'lleva el contexto canónico ya resuelto');

    // Lo que importa: entra de verdad a la sesión, no se descarta.
    var enSesion = session.documents.filter(function (d) { return d.id === 'met-task-12'; });
    t.equals(enSesion.length, 1, 'la tarea ENTRA a la sesión METIS');
    t.equals(session.risk_signals.filter(function (r) {
      return r.signal === 'CROSS_CONTEXT_RESULT_DISCARDED';
    }).length, 0, 'y no genera ningún descarte por contexto');

    // La tarea ajena sigue siendo inalcanzable.
    var ajena = ToolBroker.invoke(session, 'asana.search', { query: 'plan comercial' });
    t.equals(ajena.documents.length, 0, 'una tarea de ANDREA no aparece en una búsqueda METIS');
    t.throwsCode(Errors.CODES.PARTITION_VIOLATION, function () {
      ToolBroker.invoke(session, 'asana.get', { id: 'and-task-77' });
    }, 'y pedirla por id sigue siendo violación de partición');
  });

  TestRunner.unit('Partición', 'el modelo sólo ve lo admitido, nunca lo descartado', function (t) {
    var session = sessionFor('METIS');
    // Una fuente que devuelve algo de otro contexto pese a la partición.
    AsanaReadAdapter.useBackend({
      search: function () {
        return [{ source: 'ASANA', id: 'intruso', title: 'Tarea de otro contexto',
                  context: 'ANDREA', kind: 'TASK', epistemic_status: 'VERIFIED',
                  snippet: 'sustancia ajena' }];
      },
      get: function () { throw Errors.readFailed('ASANA', 'no aplica'); }
    });

    var result = ToolBroker.invoke(session, 'asana.search', { query: 'x' });
    t.equals(result.returned_by_source, 1, 'la fuente devolvió un documento');
    t.equals(result.documents.length, 0, 'pero el resultado que ve el modelo va vacío');
    t.equals(JSON.stringify(result).indexOf('sustancia ajena'), -1,
      'la sustancia ajena no se cuela al resultado de la herramienta');
    t.equals(session.documents.length, 0, 'ni entra a la sesión');
    t.equals(session.risk_signals[0].signal, 'CROSS_CONTEXT_RESULT_DISCARDED', 'y queda la señal de riesgo');
    Fixtures.installBackends();
  });

  // ------------------------------------------------- vigencia (registro real)
  TestRunner.unit('Vigencia', 'la relación manda; el Estado vacío usa el default declarado', function (t) {
    var vigente = RetrievalPolicy.decisionStatus({ id: 'a', estado: 'vigente', superseded_by: [] });
    t.equals(vigente.status, 'CURRENT', 'Estado vigente sin sustituta: vigente');
    t.equals(vigente.default_applied, false, 'sin default aplicado');

    // El caso mayoritario real: 202 de 316 filas no tienen `Estado`.
    var vacio = RetrievalPolicy.decisionStatus({ id: 'b', estado: null, superseded_by: [] });
    t.equals(vacio.status, 'CURRENT', 'Estado vacío sin sustituta: vigente por default declarado');
    t.equals(vacio.default_applied, true, 'y queda marcado como default aplicado');

    var sustituida = RetrievalPolicy.decisionStatus({ id: 'c', estado: 'modificada', superseded_by: ['d'] });
    t.equals(sustituida.status, 'SUPERSEDED', 'modificada con sustituta: superada');

    var vaciaSustituida = RetrievalPolicy.decisionStatus({ id: 'e', estado: null, superseded_by: ['f'] });
    t.equals(vaciaSustituida.status, 'SUPERSEDED', 'la relación basta aunque falte el Estado');
  });

  TestRunner.unit('Vigencia', 'Estado y relación en contradicción no se resuelven por juicio propio', function (t) {
    // La fila real: marcada vigente pero con `Sustituida por`.
    var contradictoria = RetrievalPolicy.decisionStatus({ id: 'a', estado: 'vigente', superseded_by: ['b'] });
    t.equals(contradictoria.status, 'CONFLICT', 'es conflicto, no un empate silencioso');
    t.equals(contradictoria.reason, 'ESTADO_VIGENTE_CON_SUSTITUTA', 'motivo nombrado');

    // La contraria: dice modificada pero no hay sustituta.
    var huerfana = RetrievalPolicy.decisionStatus({ id: 'c', estado: 'derogada', superseded_by: [] });
    t.equals(huerfana.status, 'CONFLICT', 'derogada sin sustituta también es contradicción');

    var cadena = RetrievalPolicy.closeCurrencyChain(
      [{ id: 'a', estado: 'vigente', superseded_by: ['b'] }, { id: 'b', estado: null, superseded_by: [] }], 'a');
    t.equals(cadena.closed, false, 'una cadena con conflicto no cierra');
    t.equals(cadena.reason, 'CONFLICTO_ENTRE_ESTADO_Y_RELACION', 'y dice por qué');
    t.equals(cadena.conflicts.length, 1, 'se enumera la decisión en conflicto');
  });

  TestRunner.unit('Vigencia', 'la cadena se recorre por `Sustituida por` hasta la terminal', function (t) {
    var decisiones = [
      { id: 'd1', estado: 'modificada', superseded_by: ['d2'] },
      { id: 'd2', estado: 'modificada', superseded_by: ['d3'] },
      { id: 'd3', estado: null, superseded_by: [] }
    ];
    var cerrada = RetrievalPolicy.closeCurrencyChain(decisiones, 'd1');
    t.ok(cerrada.closed, 'cierra');
    t.deepEquals(cerrada.chain, ['d1', 'd2', 'd3'], 'recorre los tres eslabones');
    t.equals(cerrada.terminal, 'd3', 'la terminal es la última');

    var incompleta = RetrievalPolicy.closeCurrencyChain(decisiones.slice(0, 2), 'd1');
    t.equals(incompleta.closed, false, 'sin el último eslabón no cierra');
    t.equals(incompleta.missing, 'd3', 'y nombra cuál falta');

    var ciclo = RetrievalPolicy.closeCurrencyChain(
      [{ id: 'x', estado: null, superseded_by: ['y'] }, { id: 'y', estado: null, superseded_by: ['x'] }], 'x');
    t.equals(ciclo.closed, false, 'un ciclo tampoco cierra');
    t.equals(ciclo.reason, 'CICLO_EN_LA_CADENA', 'se detecta como ciclo');
  });

  TestRunner.unit('Vigencia', 'la cadena cierra aunque el eslabón terminal esté en la segunda página', function (t) {
    // Páginas tal y como las devuelve la API de Notion: `has_more` + `next_cursor`.
    var paginas = {
      'INICIO': {
        results: [{ id: 'd1', superseded_by: ['d2'], estado: 'modificada' }],
        has_more: true, next_cursor: 'cursor-p2'
      },
      'cursor-p2': {
        // El terminal vive AQUÍ. Quedarse en la primera página dejaría la
        // cadena abierta y produciría una abstención falsa.
        results: [{ id: 'd2', superseded_by: [], estado: null }],
        has_more: false, next_cursor: null
      }
    };
    var pedidas = [];
    var todas = NotionReadAdapter.collectPages(function (cursor) {
      pedidas.push(cursor);
      return paginas[cursor === null ? 'INICIO' : cursor];
    });

    t.equals(pedidas.length, 2, 'se piden las dos páginas');
    t.equals(pedidas[0], null, 'la primera sin cursor');
    t.equals(pedidas[1], 'cursor-p2', 'la segunda con el next_cursor devuelto');
    t.equals(todas.length, 2, 'se acumulan los resultados de ambas');

    var cadena = RetrievalPolicy.closeCurrencyChain(todas, 'd1');
    t.ok(cadena.closed, 'la cadena CIERRA con el terminal de la segunda página');
    t.equals(cadena.terminal, 'd2', 'y el terminal es el correcto');

    // Contraprueba: con sólo la primera página, la cadena no cerraría.
    var soloPrimera = RetrievalPolicy.closeCurrencyChain(paginas.INICIO.results, 'd1');
    t.equals(soloPrimera.closed, false, 'sin paginar, la cadena quedaría abierta');
    t.equals(soloPrimera.missing, 'd2', 'y culparía a un eslabón que sí existe');
  });

  TestRunner.unit('Vigencia', 'la paginación no trunca en silencio', function (t) {
    var infinita = function () {
      return { results: [{ id: 'x' }], has_more: true, next_cursor: 'c-' + Schemas.uuid() };
    };
    t.throwsCode(Errors.CODES.READ_FAILED, function () {
      NotionReadAdapter.collectPages(infinita, 3);
    }, 'alcanzar el tope de páginas lanza en vez de devolver un conjunto truncado');

    var bucle = function () { return { results: [], has_more: true, next_cursor: 'mismo' }; };
    t.throwsCode(Errors.CODES.READ_FAILED, function () {
      NotionReadAdapter.collectPages(bucle, 10);
    }, 'un cursor repetido se detecta como bucle');

    t.equals(NotionReadAdapter.collectPages(function () {
      return { results: [1, 2], has_more: false, next_cursor: null };
    }).length, 2, 'una sola página se devuelve tal cual');
    t.equals(typeof NotionReadAdapter.MAX_PAGES, 'number', 'hay un tope defensivo declarado');
  });

  TestRunner.unit('Partición', 'Asana pagina el proyecto entero para encontrar la tarea', function (t) {
    // Un proyecto con más de 100 tareas: la buscada está en la SEGUNDA página.
    // Mirar sólo la primera informaría "no existe" sobre algo que sí está.
    var paginas = {
      'INICIO': { data: [{ gid: '1', name: 'Ritual semanal' }],
                  next_page: { offset: 'off-2' } },
      'off-2':  { data: [{ gid: '1216646666201920', name: 'Diseñar orquestación inter-modelo de Metis' }],
                  next_page: null }
    };
    var pedidas = [];
    var todas = AsanaReadAdapter.collectPages(function (offset) {
      pedidas.push(offset);
      return paginas[offset === null ? 'INICIO' : offset];
    });

    t.equals(pedidas.length, 2, 'se piden las dos páginas');
    t.equals(pedidas[0], null, 'la primera sin offset');
    t.equals(pedidas[1], 'off-2', 'la segunda con el offset devuelto');
    t.equals(todas.length, 2, 'se acumulan las tareas de ambas');
    t.equals(todas[1].gid, '1216646666201920', 'la tarea de la segunda página está presente');

    t.throwsCode(Errors.CODES.READ_FAILED, function () {
      AsanaReadAdapter.collectPages(function () {
        return { data: [{ gid: 'x' }], next_page: { offset: 'o-' + Schemas.uuid() } };
      }, 3);
    }, 'alcanzar el tope lanza en vez de truncar en silencio');

    t.throwsCode(Errors.CODES.READ_FAILED, function () {
      AsanaReadAdapter.collectPages(function () {
        return { data: [], next_page: { offset: 'mismo' } };
      }, 10);
    }, 'un offset repetido se detecta como bucle');

    t.equals(typeof AsanaReadAdapter.MAX_PAGES, 'number', 'hay un tope defensivo declarado');
  });

}
