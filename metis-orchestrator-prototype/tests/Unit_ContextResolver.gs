/** Unit_ContextResolver.gs — spec §17 / ContextResolver. */
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

})();
