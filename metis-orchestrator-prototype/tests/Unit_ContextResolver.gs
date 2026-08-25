/**
 * Unit_ContextResolver.gs — spec §17 / ContextResolver.
 *
 * Nota de ubicación: la spec §3 fija la lista de archivos de test, así que las
 * pruebas de PARTICIÓN DE FUENTES por contexto y de VIGENCIA del registro de
 * decisiones viven al final de este archivo — ambas responden a la misma
 * pregunta: qué puede ver una corrida de un contexto, y qué puede afirmar.
 */
(function () {

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

})();