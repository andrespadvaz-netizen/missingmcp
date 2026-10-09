/**
 * Bounded, read-only context capsule for Metis Lens.
 *
 * This lives in the Bridge because Lens uses a signed direct route, not the
 * productive execution queue.  It never invokes a model or a write adapter.
 */
function dispatchLensContext_(p) {
  var request = p && p.request;
  var sessionId = p && p.session_id;
  if (typeof request !== 'string' || !request.trim() ||
      Utilities.newBlob(request).getBytes().length > 3500 ||
      (sessionId !== undefined && !isLensSessionId_(sessionId))) {
    return {status: 'INVALID_REQUEST', documents: []};
  }

  var verdict = Engine.ContextResolver.resolve(request, {operator_context: null});
  // Continuity is a fallback only when this turn contains no context signal.
  // An explicit or ambiguous request can never be overridden by session state.
  if ((verdict.candidates || []).length === 0 && isLensSessionId_(sessionId)) {
    var remembered = readLensContext_(sessionId);
    if (remembered) {
      verdict = {
        candidates: [remembered], resolved_context: remembered,
        status: 'CONTEXT_RESOLVED', route: null, reason: 'SESSION_CONTINUITY'
      };
    }
  }
  if (verdict.status !== 'CONTEXT_RESOLVED' || !verdict.resolved_context) {
    return {
      status: verdict.route === 'REQUIRES_ANDRES' ? 'REQUIRES_CONTEXT' : 'ABSTAIN',
      candidates: verdict.candidates || [], documents: []
    };
  }

  var previousLevel = Engine.Config.runLevel();
  try {
    // A live Engine can already be in its read-only level while serving its
    // own health/recovery work.  Lens is compatible with LEVEL_0 and LEVEL_1:
    // it never writes or invokes a model, and the exact previous level is
    // restored in finally.  Only a productive/unknown level is unsafe here.
    if (previousLevel !== Engine.Config.LEVELS.LEVEL_0 &&
        previousLevel !== Engine.Config.LEVELS.LEVEL_1) {
      return {status: 'UNAVAILABLE', documents: []};
    }
    if (previousLevel === Engine.Config.LEVELS.LEVEL_0) {
      Engine.Config._setRunLevel(Engine.Config.LEVELS.LEVEL_1);
    }
    var context = verdict.resolved_context;
    var session = Engine.ToolBroker.newSession(
      {execution_id: 'lens-' + Utilities.getUuid()},
      Engine.ContextResolver.scopeFor([context], context),
      Engine.AuthorityPolicy.preRetrievalGrant([context])
    );
    var sourceSpecs = [
      {source: 'NOTION', search: 'notion.search', fetch: 'notion.fetch'},
      {source: 'DRIVE', search: 'drive.search', fetch: 'drive.fetch'}
    ];
    var documents = [];
    var sources = [];
    // A neutral request deliberately uses an empty query.  The adapter remains
    // partition-scoped, and results are ordered by their provider update time.
    var query = lensSearchQuery_(request);
    for (var i = 0; i < sourceSpecs.length && documents.length < 4; i++) {
      var spec = sourceSpecs[i];
      if (!Engine.Config.partitionFor(context, spec.source)) {
        sources.push({source: spec.source, status: 'OMITTED', documents: 0});
        continue;
      }
      try {
        var found = Engine.ToolBroker.invoke(session, spec.search, {query: query, page_size: 8});
        var candidates = (found && found.documents ? found.documents : []).slice();
        candidates.sort(lensRecentFirst_);
        var admitted = 0;
        for (var d = 0; d < candidates.length && documents.length < 4; d++) {
          var fetched = Engine.ToolBroker.invoke(session, spec.fetch, {id: candidates[d].id});
          var item = fetched && fetched.documents && fetched.documents[0];
          if (item && typeof item.snippet === 'string' && item.snippet.trim()) {
            documents.push({
              context: context, source: spec.source, id: item.id,
              title: item.title || null, kind: item.kind || null,
              epistemic_status: item.epistemic_status || null,
              content: item.snippet.slice(0, 1200),
              updated_at: item.updated_at || candidates[d].updated_at || null,
              starts_at: item.starts_at || candidates[d].starts_at || null
            });
            admitted++;
          }
        }
        sources.push({source: spec.source, status: 'READY', documents: admitted});
      } catch (_) {
        sources.push({source: spec.source, status: 'UNAVAILABLE', documents: 0});
      }
    }
    if (documents.length > 0 && isLensSessionId_(sessionId)) {
      rememberLensContext_(sessionId, context);
    }
    return {
      status: 'READY', resolved_context: context, documents: documents,
      sources: sources, writes_attempted: 0, models_invoked: 0
    };
  } catch (_) {
    return {status: 'UNAVAILABLE', documents: []};
  } finally {
    Engine.Config._setRunLevel(previousLevel);
  }
}

function lensSearchQuery_(request) {
  var normalized = Engine.ContextResolver.normalize(request || '');
  // Context labels and generic recency phrasing are not document titles.
  if (/^(contexto\s*:\s*[^.]+[.]?\s*)?¿?(que|qué)\s+(fue|es)\s+lo\s+(ultimo|último)/.test(normalized)) {
    return '';
  }
  return request;
}

function lensRecentFirst_(a, b) {
  var av = String((a && (a.updated_at || a.starts_at)) || '');
  var bv = String((b && (b.updated_at || b.starts_at)) || '');
  if (av === bv) { return String((a && a.id) || '').localeCompare(String((b && b.id) || '')); }
  return av > bv ? -1 : 1;
}

var LENS_CONTEXT_PREFIX_ = 'metis_lens_context_';
var LENS_CONTEXT_TTL_MS_ = 30 * 60 * 1000;
function isLensSessionId_(value) {
  return typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
}
function readLensContext_(sessionId) {
  var props = PropertiesService.getScriptProperties();
  var raw = props.getProperty(LENS_CONTEXT_PREFIX_ + sessionId);
  if (!raw) { return null; }
  try {
    var record = JSON.parse(raw);
    if (!record || typeof record.context !== 'string' || record.expires_at <= Date.now() ||
        Engine.Config.contextNames().indexOf(record.context) < 0) {
      props.deleteProperty(LENS_CONTEXT_PREFIX_ + sessionId);
      return null;
    }
    return record.context;
  } catch (_) {
    props.deleteProperty(LENS_CONTEXT_PREFIX_ + sessionId);
    return null;
  }
}
function rememberLensContext_(sessionId, context) {
  PropertiesService.getScriptProperties().setProperty(
    LENS_CONTEXT_PREFIX_ + sessionId,
    JSON.stringify({context: context, expires_at: Date.now() + LENS_CONTEXT_TTL_MS_})
  );
}
