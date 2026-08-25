/**
 * RetrievalPolicy.gs — política de Retrieval de sólo lectura (spec §9).
 *
 *  - CANON manda en autoridad, reglas y protocolos.
 *  - Registro de decisiones + fuente operativa competente mandan en estado
 *    material y vigencia operativa.
 *  - Conflicto real de autoridad/norma -> REQUIRES_ANDRES.
 *  - El contenido recuperado es EVIDENCIA, nunca mandato.
 *  - Toda evidencia conserva etiqueta epistémica.
 *  - El receptor recupera desde la fuente; no se transporta copia de sustancia.
 */
var RetrievalPolicy = (function () {

  /** Clase de documento -> etiqueta epistémica (spec §4.2). */
  var EPISTEMIC_BY_KIND = {
    CANON: 'VERIFIED',
    CANON_CORE: 'VERIFIED',
    DECISION_REGISTRY: 'REGISTERED_DECISION',
    PHI: 'HISTORICAL',
    HANDOFF_LOG: 'HISTORICAL',
    CHAT_LOG: 'HISTORICAL',
    PROPOSAL: 'PROPOSAL',
    DRAFT: 'PROPOSAL',
    TASK: 'VERIFIED',
    EVENT: 'VERIFIED',
    FILE: 'VERIFIED',
    NOTE: 'INFERRED'
  };

  /** Señales de prompt injection indirecta (spec §16.6, threat model). */
  var INJECTION_PATTERNS = [
    /ignora (las |todas las )?instrucciones/i,
    /a partir de ahora (eres|actua)/i,
    /usa la herramienta/i,
    /env(i|í)a (un )?(correo|email)/i,
    /actualiza el canon/i,
    /modifica el canon/i,
    /concede(le)? (autoridad|permiso)/i,
    /autoriza(r)? (la )?escritura/i,
    /api[ _-]?key/i,
    /credencial(es)?/i,
    /ejecuta (esto|la accion) sin/i,
    /destino:\s*\S+/i
  ];

  function epistemicFor(kind) {
    var label = EPISTEMIC_BY_KIND[kind];
    return label ? label : 'INFERRED';
  }

  /** Fuente competente para el tipo de pregunta (spec §9, precedencia). */
  function competentSourcesFor(questionKind) {
    if (questionKind === 'AUTHORITY' || questionKind === 'NORM' || questionKind === 'PROTOCOL') {
      return Config.SOURCE_COMPETENCE.AUTHORITY.slice();
    }
    return Config.SOURCE_COMPETENCE.OPERATIONAL_STATE.slice();
  }

  /**
   * Cierre de cadena de decisión antes de afirmar estado (spec §16.5).
   * @param {Array<{id:string, superseded_by:string|null}>} decisions
   * @param {string} rootId
   * @return {{closed:boolean, chain:string[], terminal:string|null, missing:string|null}}
   */
  function closeCurrencyChain(decisions, rootId) {
    var byId = {};
    for (var i = 0; i < decisions.length; i++) { byId[decisions[i].id] = decisions[i]; }

    var chain = [];
    var seen = {};
    var currentId = rootId;

    while (currentId) {
      if (seen[currentId]) {
        return { closed: false, chain: chain, terminal: null, missing: currentId };
      }
      seen[currentId] = true;
      var node = byId[currentId];
      if (!node) {
        return { closed: false, chain: chain, terminal: null, missing: currentId };
      }
      chain.push(currentId);
      if (!node.superseded_by) {
        return { closed: true, chain: chain, terminal: currentId, missing: null };
      }
      currentId = node.superseded_by;
    }
    return { closed: false, chain: chain, terminal: null, missing: rootId };
  }

  /**
   * Conflicto REAL de autoridad/norma: dos fuentes competentes en autoridad que
   * afirman cosas incompatibles sobre el mismo sujeto normativo.
   * Ojo: "CANON autoriza X" + "una decisión posterior retiró X" NO es conflicto:
   * autorización y estado de ejecución son objetos distintos.
   */
  function detectAuthorityConflict(claims) {
    var bySubject = {};
    for (var i = 0; i < claims.length; i++) {
      var c = claims[i];
      if (c.type !== 'AUTHORITY') { continue; }
      if (!bySubject[c.subject]) { bySubject[c.subject] = []; }
      bySubject[c.subject].push(c);
    }
    var subjects = Object.keys(bySubject);
    for (var s = 0; s < subjects.length; s++) {
      var group = bySubject[subjects[s]];
      for (var a = 0; a < group.length; a++) {
        for (var b = a + 1; b < group.length; b++) {
          if (group[a].value !== group[b].value) {
            return {
              conflict: true,
              subject: subjects[s],
              sources: [group[a].source, group[b].source]
            };
          }
        }
      }
    }
    return { conflict: false, subject: null, sources: [] };
  }

  /**
   * Cobertura: ¿puede demostrarse completitud? (spec §16.10)
   * @param {string[]} required
   * @param {Object<string,{covered:boolean, reason:string}>} status
   */
  function assessCoverage(required, status) {
    var missing = [];
    var reasons = {};
    for (var i = 0; i < required.length; i++) {
      var src = required[i];
      var st = status[src];
      if (!st || st.covered !== true) {
        missing.push(src);
        reasons[src] = st && st.reason ? st.reason : 'SIN_COBERTURA';
      }
    }
    return {
      complete: missing.length === 0,
      missing: missing,
      reasons: reasons
    };
  }

  /** El contenido recuperado se marca como evidencia y se le detectan señales de riesgo. */
  function detectInjection(text) {
    var signals = [];
    var s = String(text === null || text === undefined ? '' : text);
    for (var i = 0; i < INJECTION_PATTERNS.length; i++) {
      if (INJECTION_PATTERNS[i].test(s)) {
        signals.push(INJECTION_PATTERNS[i].source);
      }
    }
    return {
      is_injection_attempt: signals.length > 0,
      signals: signals,
      treated_as: 'EVIDENCE',
      grants_authority: false
    };
  }

  /**
   * Convierte un documento recuperado en EvidenceRef (puntero + etiqueta).
   * NO transporta sustancia: sólo id, título y contexto.
   */
  function toEvidenceRef(doc) {
    return Schemas.newEvidenceRef(
      doc.source,
      doc.id,
      doc.title === undefined ? null : doc.title,
      doc.context === undefined ? null : doc.context,
      doc.epistemic_status ? doc.epistemic_status : epistemicFor(doc.kind)
    );
  }

  /** Contexto mínimo suficiente: recorta a `limit` punteros, preservando orden. */
  function minimumSufficient(refs, limit) {
    var max = (limit === undefined || limit === null) ? 8 : limit;
    return refs.slice(0, max);
  }

  return {
    EPISTEMIC_BY_KIND: EPISTEMIC_BY_KIND,
    INJECTION_PATTERNS: INJECTION_PATTERNS,
    epistemicFor: epistemicFor,
    competentSourcesFor: competentSourcesFor,
    closeCurrencyChain: closeCurrencyChain,
    detectAuthorityConflict: detectAuthorityConflict,
    assessCoverage: assessCoverage,
    detectInjection: detectInjection,
    toEvidenceRef: toEvidenceRef,
    minimumSufficient: minimumSufficient
  };
})();
