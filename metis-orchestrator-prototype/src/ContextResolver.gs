/**
 * ContextResolver.gs — resolución de contexto e intención (spec §6.3, §16.4, §17).
 *
 * Invariante central: NO se recupera sustancia de dos contextos incompatibles
 * para "desambiguar". Mientras haya más de un candidato material, el alcance de
 * lectura queda degradado a metadatos (título/id/contexto) y cualquier lectura
 * de sustancia queda prohibida por el ToolBroker.
 *
 * Si la ambigüedad no se cierra sin contaminación:
 *   - 0 candidatos      -> ABSTAIN (no se puede demostrar contexto)
 *   - >1 incompatibles  -> REQUIRES_ANDRES (el dato desambiguador vive en el
 *                          turno del operador, no en una fuente consultable)
 */
var ContextResolver = (function () {

  function normalize(text) {
    var s = String(text === null || text === undefined ? '' : text).toLowerCase();
    s = s.replace(/[áàä]/g, 'a').replace(/[éèë]/g, 'e').replace(/[íìï]/g, 'i')
         .replace(/[óòö]/g, 'o').replace(/[úùü]/g, 'u').replace(/ñ/g, 'n');
    return s;
  }

  /**
   * Detecta candidatos de contexto SÓLO a partir del turno vivo del operador.
   * Nunca a partir de contenido recuperado (spec §6.2).
   */
  function detectCandidates(operatorRequest, options) {
    var opts = options || {};
    var found = [];
    if (opts.operator_context) {
      // Instrucción expresa del operador: contexto declarado, no inferido.
      return [opts.operator_context];
    }
    var text = normalize(operatorRequest);
    var names = Config.contextNames();
    for (var i = 0; i < names.length; i++) {
      var signals = Config.CONTEXTS[names[i]].signals;
      for (var j = 0; j < signals.length; j++) {
        if (text.indexOf(normalize(signals[j])) !== -1) {
          if (found.indexOf(names[i]) === -1) { found.push(names[i]); }
          break;
        }
      }
    }
    return found;
  }

  /** Dos contextos distintos son incompatibles por default (separación de contexto). */
  function isIncompatible(a, b) { return a !== b; }

  function hasIncompatiblePair(candidates) {
    for (var i = 0; i < candidates.length; i++) {
      for (var j = i + 1; j < candidates.length; j++) {
        if (isIncompatible(candidates[i], candidates[j])) { return true; }
      }
    }
    return false;
  }

  var INTENT_SIGNALS = [
    { intent: 'audit',      words: ['audita', 'auditoria', 'auditar', 'pac demolicion', 'revision independiente'] },
    { intent: 'execution',  words: ['actualiza', 'crea ', 'envia', 'publica', 'modifica', 'mueve', 'archiva', 'agenda ', 'escribe en'] },
    { intent: 'decision',   words: ['decide', 'decidir', 'aprueba', 'ratifica'] },
    { intent: 'creation',   words: ['disena', 'redacta', 'borrador', 'propon', 'escribe un'] },
    { intent: 'analysis',   words: ['analiza', 'compara', 'evalua', 'diagnostica'] },
    { intent: 'retrieval',  words: ['que ', 'cual', 'estado', 'encuentra', 'busca', 'donde', 'sigue activa', 'sigue vigente'] }
  ];

  function detectIntent(operatorRequest) {
    var text = normalize(operatorRequest);
    for (var i = 0; i < INTENT_SIGNALS.length; i++) {
      var words = INTENT_SIGNALS[i].words;
      for (var j = 0; j < words.length; j++) {
        if (text.indexOf(normalize(words[j])) !== -1) { return INTENT_SIGNALS[i].intent; }
      }
    }
    return 'retrieval';
  }

  /**
   * Alcance de lectura previo a Retrieval (spec §6.3, fase 1).
   * `substance_allowed` sólo es true cuando el contexto ya está resuelto.
   */
  function scopeFor(candidates, resolvedContext) {
    return {
      candidates: candidates.slice(),
      resolved: resolvedContext || null,
      substance_allowed: !!resolvedContext && candidates.length <= 1
    };
  }

  /**
   * Resuelve contexto. Devuelve un veredicto explícito; nunca adivina.
   * @return {{candidates:string[], resolved_context:string|null, status:string,
   *           route:string|null, reason:string, scope:object}}
   */
  function resolve(operatorRequest, options) {
    var candidates = detectCandidates(operatorRequest, options);

    if (candidates.length === 0) {
      return {
        candidates: candidates,
        resolved_context: null,
        status: 'UNCERTAIN',
        route: 'ABSTAIN',
        reason: 'NO_CONTEXT_SIGNAL',
        scope: scopeFor(candidates, null)
      };
    }

    if (candidates.length > 1 && hasIncompatiblePair(candidates)) {
      return {
        candidates: candidates,
        resolved_context: null,
        status: 'REQUIRES_ANDRES',
        route: 'REQUIRES_ANDRES',
        reason: 'CONTEXT_AMBIGUOUS',
        scope: scopeFor(candidates, null)
      };
    }

    return {
      candidates: candidates,
      resolved_context: candidates[0],
      status: 'CONTEXT_RESOLVED',
      route: null,
      reason: 'SINGLE_CONTEXT',
      scope: scopeFor(candidates, candidates[0])
    };
  }

  /**
   * Guarda usada por el ToolBroker antes de servir cualquier lectura.
   * Lanza si se intenta leer sustancia con el contexto sin resolver o si se
   * intenta leer fuera del contexto resuelto.
   */
  function assertReadAllowed(scope, requestedContext, wantsSubstance) {
    if (wantsSubstance && !scope.substance_allowed) {
      throw Errors.contextAmbiguous(scope.candidates);
    }
    if (requestedContext) {
      if (scope.resolved) {
        if (requestedContext !== scope.resolved) {
          throw Errors.authorityDenied('Lectura fuera del contexto resuelto', {
            requested: requestedContext, resolved: scope.resolved
          });
        }
      } else if (scope.candidates.length && scope.candidates.indexOf(requestedContext) === -1) {
        throw Errors.authorityDenied('Lectura fuera de los contextos candidatos', {
          requested: requestedContext, candidates: scope.candidates
        });
      }
    }
    return true;
  }

  return {
    normalize: normalize,
    detectCandidates: detectCandidates,
    detectIntent: detectIntent,
    isIncompatible: isIncompatible,
    hasIncompatiblePair: hasIncompatiblePair,
    scopeFor: scopeFor,
    resolve: resolve,
    assertReadAllowed: assertReadAllowed
  };
})();
