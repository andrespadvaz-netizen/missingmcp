/**
 * Read-only, partition-scoped retrieval capsules for Metis Lens.
 *
 * This is deliberately separate from Orchestrator.run(): Lens never invokes a
 * model, constructs a plan, or accepts a context supplied by retrieved text.
 */
var LensContext = (function () {
  var READ_TOOLS = ['notion.search', 'notion.decisions', 'asana.search',
                    'drive.search', 'calendar.read'];

  function _grant(context) {
    return { allowed_tools: READ_TOOLS.slice(), allowed_contexts: [context],
      forbidden_effects: Config.FORBIDDEN_EFFECTS.slice() };
  }

  function _recentValue(doc) {
    return doc.updated_at || doc.starts_at || '';
  }

  function _recentFirst(a, b) {
    var av = _recentValue(a), bv = _recentValue(b);
    if (av === bv) { return String(a.id).localeCompare(String(b.id)); }
    return av > bv ? -1 : 1;
  }

  /**
   * Build a bounded evidence capsule. `continuityContext` is accepted only
   * when the current request names no candidate; an explicit request always
   * wins, and an unknown or conflicting context never falls back.
   */
  function capsule(request, continuityContext) {
    var resolved = ContextResolver.resolve(request, {});
    var context = resolved.resolved_context;
    var usedContinuity = false;
    if (!context && resolved.candidates.length === 0 && continuityContext &&
        Config.contextNames().indexOf(continuityContext) >= 0) {
      context = continuityContext;
      usedContinuity = true;
    }
    if (!context) {
      return { status: resolved.status === 'REQUIRES_ANDRES' ? 'REQUIRES_CONTEXT' : 'NO_CONTEXT',
        resolved_context: null, continuity_context: null, documents: [], sources: {},
        reason: resolved.reason };
    }

    var scope = ContextResolver.scopeFor([context], context);
    var session = ToolBroker.newSession({ execution_id: 'lens' }, scope, _grant(context));
    var windowEnd = new Date();
    var windowStart = new Date(windowEnd.getTime() - 31 * 24 * 60 * 60 * 1000);
    var args = {
      // The request is for context selection, not a full-text predicate. A
      // natural-language question rarely matches a source title verbatim;
      // retrieve the bounded, already-partitioned recent set instead.
      'notion.search': { query: '' },
      'notion.decisions': {},
      'asana.search': { query: '' },
      'drive.search': { query: '' },
      'calendar.read': { query: '', time_window: { start: windowStart.toISOString(), end: windowEnd.toISOString() } }
    };
    var sources = {};
    for (var i = 0; i < READ_TOOLS.length; i++) {
      var tool = READ_TOOLS[i];
      try {
        var result = ToolBroker.invoke(session, tool, args[tool]);
        sources[result.source || tool] = result.coverage === true ? 'COVERED' : 'UNAVAILABLE';
      } catch (e) {
        // Missing partitions and source failures are deliberately represented as
        // unavailable rather than replaced by a broader, unsafe read.
        var source = tool.split('.')[0].toUpperCase();
        sources[source] = 'UNAVAILABLE';
      }
    }
    var docs = session.documents.slice().sort(_recentFirst).slice(0, 24).map(function (d) {
      return { source: d.source, id: d.id, title: d.title, context: d.context,
        kind: d.kind, epistemic_status: d.epistemic_status, snippet: d.snippet || null,
        updated_at: d.updated_at || null, starts_at: d.starts_at || null };
    });
    return { status: 'OK', resolved_context: context, continuity_context: context,
      continued: usedContinuity, documents: docs, sources: sources };
  }

  return { capsule: capsule };
})();
