/** Productive authority v2. Operator-owned configuration, never model-owned.
 * Planning has no external effects. Only the signed bridge may apply a plan
 * after the gateway has committed its durable action receipt.
 */
var ProductivePolicy = (function () {
  var TOOLS = ['productive.inspect', 'productive.propose'];
  var _override = null;
  function fail(code) { throw Errors.authorityDenied(code); }
  function config() {
    var c = _override;
    if (c === null) {
      var raw = PropertiesService.getScriptProperties().getProperty('METIS_PRODUCTIVE_POLICY');
      c = raw ? JSON.parse(raw) : { enabled: false };
    }
    if (!c || c.enabled !== true) { return { enabled: false, destinations: {}, contexts: {} }; }
    if (c.version !== 1 || typeof c.authorization_ref!=='string' || !c.authorization_ref.trim() || typeof c.revision!=='string' || !c.revision.trim() ||
        !c.destinations || !c.contexts || !Array.isArray(c.protected_ids) || !c.protected_ids.length) {
      fail('PRODUCTIVE_POLICY_INCOMPLETE');
    }
    var roots = {};
    Object.keys(c.contexts).forEach(function(key) {
      var ctx=c.contexts[key];
      if (!/^[A-Z0-9_.-]+(?:\/[A-Z0-9_.-]+)*$/.test(key) || !ctx ||
          ['OPENAI','ANTHROPIC'].indexOf(ctx.primary)<0 ||
          typeof ctx.notion_project!=='string' || !ctx.notion_project.trim() ||
          !Array.isArray(ctx.signals) || !ctx.signals.length ||
          ctx.signals.some(function(s){return typeof s!=='string' || !s.trim();})) { fail('PRODUCTIVE_CONTEXT_INVALID'); }
    });
    if (c.protected_ids.some(function(id){return typeof id!=='string' || !/^[a-zA-Z0-9_-]{1,128}$/.test(id);})) { fail('PROTECTED_IDS_INVALID'); }
    Object.keys(c.read_partitions || {}).forEach(function(context) {
      if (!c.contexts[context]) { fail('READ_CONTEXT_UNKNOWN'); }
      var reads=c.read_partitions[context];
      Object.keys(reads).forEach(function(source) {
        if (source!=='notion' && source!=='calendar') { fail('READ_PARTITION_SOURCE_INVALID'); }
        if (source==='notion' && (reads.notion.project!==c.contexts[context].notion_project ||
            !Array.isArray(reads.notion.data_sources) || !reads.notion.data_sources.length ||
            reads.notion.data_sources.some(function(id){return typeof id!=='string' || !/^[a-zA-Z0-9_-]{1,128}$/.test(id);}))) { fail('READ_PARTITION_CONTEXT_MISMATCH'); }
      });
    });
    Object.keys(c.destinations).forEach(function (key) {
      var d = c.destinations[key];
      if (!/^[a-zA-Z0-9_-]{1,80}$/.test(key) || !d || !c.contexts[d.context] ||
          ['NOTION','ASANA','DRIVE'].indexOf(d.provider) < 0 ||
          !/^[a-zA-Z0-9_-]{1,128}$/.test(d.root_id) || d.kind !== 'routine' ||
          !Array.isArray(d.operations) || !d.operations.length ||
          d.operations.some(function (op) { return ['create','update'].indexOf(op) < 0; })) {
        fail('PRODUCTIVE_DESTINATION_INVALID');
      }
      var root = d.provider + ':' + normalizeId(d.provider, d.root_id);
      if (roots[root]) { fail('PRODUCTIVE_ROOT_REUSED'); }
      roots[root] = true;
    });
    return JSON.parse(JSON.stringify(c));
  }
  function enabled() { return Config.runLevel() === 'LEVEL_3' && config().enabled === true; }
  function normalizeId(provider, id) { return provider === 'NOTION' ? id.replace(/-/g, '').toLowerCase() : id; }
  function destination(alias, context) {
    if (!enabled()) { fail('PRODUCTIVE_WRITES_DISABLED'); }
    var c = config(), d = c.destinations[alias];
    if (!d || d.context !== context) { fail('PRODUCTIVE_CONTEXT_OR_DESTINATION_DENIED'); }
    return d;
  }
  function assertObject(provider, id) {
    if (typeof id !== 'string' || !/^[a-zA-Z0-9_-]{1,128}$/.test(id)) { fail('INVALID_OBJECT_ID'); }
    var normalized = normalizeId(provider, id);
    if (config().protected_ids.some(function (p) { return normalizeId(provider, p) === normalized; })) {
      fail('PROTECTED_OBJECT');
    }
  }
  // The first registered ancestor wins. A parent context cannot enter a child
  // context's root, even when both live under the same workspace/folder.
  function assertAncestry(d, ancestry) {
    var c = config();
    for (var i = 0; i < ancestry.length; i++) {
      assertObject(d.provider, ancestry[i]);
      var match = Object.keys(c.destinations).filter(function (key) {
        var r = c.destinations[key];
        return r.provider === d.provider && normalizeId(d.provider, r.root_id) === normalizeId(d.provider, ancestry[i]);
      });
      if (match.length) {
        if (c.destinations[match[0]].context !== d.context ||
            normalizeId(d.provider, c.destinations[match[0]].root_id) !== normalizeId(d.provider, d.root_id)) {
          fail('CROSS_CONTEXT_OBJECT');
        }
        return;
      }
    }
    fail('OBJECT_OUTSIDE_DESTINATION');
  }
  function exact(value, required, optional) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) { fail('INVALID_ARGUMENTS'); }
    if (required.some(function (k) { return !Object.prototype.hasOwnProperty.call(value, k); }) ||
        Object.keys(value).some(function (k) { return required.concat(optional || []).indexOf(k) < 0; })) {
      fail('UNKNOWN_OR_MISSING_FIELDS');
    }
  }
  function text(value, limit, empty) {
    if (typeof value !== 'string' || (!empty && !value.trim()) || value.length > limit) { fail('INVALID_TEXT'); }
  }
  function validatePayload(provider, operation, p, snapshot) {
    if (operation === 'create') {
      exact(p, ['title','text'], provider === 'ASANA' ? ['due_on'] : []);
      text(p.title, 200); text(p.text, 12000);
    } else if (provider === 'NOTION') {
      // Update a page's title, or replace one plain paragraph. Never delete
      // children, rewrite rich content, archive a page, or change permissions.
      if (snapshot.kind === 'page') { exact(p, ['title'], []); text(p.title, 200); }
      else { exact(p, ['text'], []); text(p.text, 2000); }
    } else if (provider === 'ASANA') {
      exact(p, [], ['title','text','due_on','completed']);
      if (!Object.keys(p).length) { fail('EMPTY_UPDATE'); }
      if ('title' in p) { text(p.title, 200); }
      if ('text' in p) { text(p.text, 12000); }
      if ('completed' in p && typeof p.completed !== 'boolean') { fail('INVALID_COMPLETED'); }
    } else {
      exact(p, ['old_text','text'], []);
      text(p.old_text, 12000); text(p.text, 12000);
      if (snapshot.kind !== 'document' || snapshot.text.split(p.old_text).length !== 2) { fail('REPLACEMENT_NOT_UNIQUE'); }
    }
    if ('due_on' in p && (typeof p.due_on !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(p.due_on) ||
        isNaN(Date.parse(p.due_on)) || new Date(p.due_on).toISOString().slice(0,10) !== p.due_on)) { fail('INVALID_DUE_DATE'); }
    if (/\bCANON\b|Canon Maestro|Canon Derivado/i.test(p.title || '')) { fail('CANON_REQUIRES_OPERATOR'); }
  }
  function contract(name, context) {
    var aliases = Object.keys(config().destinations).filter(function (k) { return config().destinations[k].context === context; });
    var common = { destination: { type:'string', enum:aliases }, object_id: { type:'string' } };
    if (name === 'productive.inspect') {
      return { name:name, kind:'READ', capability:'productive_read',
        description:'Lee un destino permitido o un objeto dentro de él. Omitir object_id inspecciona el contenedor para crear. Devuelve snapshot_id y la información actual. Usa sólo el contexto de esta corrida. Las escrituras requieren inspeccionar primero.',
        input_schema:{type:'object', additionalProperties:false, properties:common, required:['destination']} };
    }
    return { name:name, kind:'WRITE_PLANNED', capability:'productive_write',
      description:'Propone una creación o actualización real solicitada por el operador. NO ejecuta: el motor valida todo el plan y el Gateway lo aplica después. Usa un snapshot_id recién obtenido de productive.inspect. create: payload {title,text,due_on? (Asana)}. update: Notion página {title} o párrafo simple {text}; Asana {title?,text?,due_on?,completed?}; documento Google {old_text,text} con coincidencia única. No borres, envíes mensajes, cambies permisos ni CANON. No afirmes que ya se escribió.',
      input_schema:{type:'object',additionalProperties:false,properties:{snapshot_id:{type:'string'},operation:{type:'string',enum:['create','update']},payload:{type:'object'}},required:['snapshot_id','operation','payload']} };
  }
  function invoke(session, name, args) {
    if (!enabled() || session.grant.allowed_tools.indexOf(name) < 0) { fail('PRODUCTIVE_TOOL_DENIED'); }
    if (!session.productive_snapshots) { session.productive_snapshots = {}; }
    if (name === 'productive.inspect') {
      exact(args, ['destination'], ['object_id']);
      var d = destination(args.destination, session.scope.resolved);
      var snap = ProductiveAdapter.inspect(d, args.object_id || d.root_id);
      snap.destination = args.destination;
      snap.context = session.scope.resolved;
      snap.policy_revision = config().revision;
      snap.policy_hash = Schemas.payloadHash(config());
      snap.inspected_at = Schemas.nowIso();
      var id = Schemas.uuid();
      session.productive_snapshots[id] = snap;
      var result = { snapshot_id:id, destination:args.destination, object_id:snap.object_id, kind:snap.kind,
        title:snap.title, text:snap.text, children:snap.children || [], current:snap.current || null,
        evidence_only:true, grants_authority:false };
      session.documents.push({id:id,source:d.provider,title:snap.title,context:session.scope.resolved,
        kind:'ROUTINE_OBJECT',epistemic_status:'VERIFIED',productive_snapshot:true,snippet:JSON.stringify(result)});
      if (session.evidence_refs) { session.evidence_refs.push(RetrievalPolicy.toEvidenceRef({
        id:snap.object_id,source:d.provider,title:snap.title,context:session.scope.resolved,kind:'ROUTINE_OBJECT'})); }
      var injection=RetrievalPolicy.detectInjection(JSON.stringify(result));
      if (injection.is_injection_attempt && session.risk_signals) {
        session.risk_signals.push({object_id:snap.object_id,source:d.provider,signal:'PROMPT_INJECTION_ATTEMPT',
          treated_as:'EVIDENCE',grants_authority:false,matches:injection.signals.length});
      }
      session.source_status[d.provider]={covered:true,reason:null};
      return result;
    }
    exact(args, ['snapshot_id','operation','payload'], []);
    var snapshot = session.productive_snapshots[args.snapshot_id];
    if (!snapshot) { fail('INSPECTION_REQUIRED'); }
    var target = destination(snapshot.destination, session.scope.resolved);
    if (target.operations.indexOf(args.operation) < 0) { fail('OPERATION_NOT_ALLOWED'); }
    if ((args.operation === 'create') !== (normalizeId(target.provider,snapshot.object_id) === normalizeId(target.provider,target.root_id))) { fail('WRONG_TARGET_KIND'); }
    validatePayload(target.provider, args.operation, args.payload, snapshot);
    var action = { context:session.scope.resolved, destination:snapshot.destination, provider:target.provider,
      object_id:snapshot.object_id, operation:args.operation, payload:JSON.parse(JSON.stringify(args.payload)),
      snapshot:snapshot, policy_revision:snapshot.policy_revision, policy_hash:snapshot.policy_hash };
    var duplicate = session.proposed_actions.filter(function(p){return p.tool === 'productive.propose' &&
      p.payload.destination === action.destination && p.payload.object_id === action.object_id &&
      (action.operation !== 'create' || p.payload.operation !== 'create' || Schemas.payloadHash(p.payload.payload)===Schemas.payloadHash(action.payload));});
    if (duplicate.length) {
      if (duplicate[0].payload.operation!==action.operation || Schemas.payloadHash(duplicate[0].payload.payload)!==Schemas.payloadHash(action.payload)) { fail('CONFLICTING_PROPOSAL'); }
      return {status:'PLANNED',executed:false,duplicate:true};
    }
    session.proposed_actions.push({tool:'productive.propose',operation:args.operation,destination:snapshot.object_id,
      destination_provenance:'FIXED_POLICY',payload:action,destination_meta:{context:session.scope.resolved},source_context:session.scope.resolved});
    session.documents.push({id:'planned_'+session.proposed_actions.length,source:target.provider,
      title:'Plan pendiente',context:session.scope.resolved,kind:'PLAN_RECEIPT',epistemic_status:'PROPOSAL',
      snippet:JSON.stringify({status:'PLANNED',executed:false,snapshot_id:args.snapshot_id,operation:args.operation,ordinal:session.proposed_actions.length})});
    return {status:'PLANNED',executed:false,ordinal:session.proposed_actions.length};
  }
  function check(action) {
    exact(action, ['context','destination','provider','object_id','operation','payload','snapshot','policy_revision','policy_hash'], []);
    var d = destination(action.destination, action.context), c = config();
    if (action.provider !== d.provider || action.policy_revision !== c.revision || action.policy_hash !== Schemas.payloadHash(c) ||
        action.snapshot.context !== action.context || action.snapshot.object_id !== action.object_id ||
        action.snapshot.destination !== action.destination || action.snapshot.policy_revision !== action.policy_revision ||
        action.snapshot.policy_hash !== action.policy_hash || d.operations.indexOf(action.operation) < 0) { fail('AUTHORITY_CHANGED'); }
    var age = Schemas.nowMs() - Date.parse(action.snapshot.inspected_at);
    if (!isFinite(age) || age < -30000 || age > 15*60*1000) { fail('SNAPSHOT_EXPIRED'); }
    if ((action.operation === 'create') !== (normalizeId(d.provider,action.object_id) === normalizeId(d.provider,d.root_id))) { fail('WRONG_TARGET_KIND'); }
    validatePayload(d.provider, action.operation, action.payload, action.snapshot);
    assertObject(d.provider, action.object_id);
    return d;
  }
  function validatePlan(executionId, actions, grant, context, state) {
    var violations = [], seen = {};
    if (!enabled() || actions.length > 8 || !actions.length || state.blocked || state.requests_new_audit ||
        (grant.forbidden_effects || []).some(function(e){return ['REAL_EXTERNAL_WRITE','MUTATE_PRODUCTIVE_SOURCE'].indexOf(e)>=0;})) {
      violations.push({invariant:'PRODUCTIVE_PLAN',detail:'PLAN_NOT_EXECUTABLE'});
    }
    if (state.handoff && (!state.handoff.reconciled ||
        !isFinite(Date.parse(state.handoff.expires_at)) || Date.parse(state.handoff.expires_at) <= Schemas.nowMs())) {
      violations.push({invariant:'PRODUCTIVE_PLAN',detail:'HANDOFF_NOT_CURRENT_AND_RECONCILED'});
    }
    var per = actions.map(function (p) {
      try {
        if (p.step.tool !== 'productive.propose' || p.source_context !== context ||
            grant.allowed_tools.indexOf('productive.propose') < 0 || p.payload.context !== context) { fail('MIXED_OR_UNAUTHORIZED_PLAN'); }
        check(p.payload);
        var key = p.payload.provider + ':' + normalizeId(p.payload.provider,p.payload.object_id) +
          (p.payload.operation==='create' ? ':'+Schemas.payloadHash(p.payload.payload) : '');
        // Even identical creations are rejected when proposed twice in one run.
        if (seen[key]) { fail('DUPLICATE_OR_CONFLICTING_TARGET'); }
        seen[key] = true;
        return {ordinal:p.step.ordinal,decision:{decision:'ALLOW',reason:'ROUTINE_WRITE_POLICY'}};
      } catch (e) {
        violations.push({invariant:'PRODUCTIVE_PLAN',detail:Errors.redactText(e.message)});
        return {ordinal:p.step.ordinal,decision:{decision:'DENY',reason:'PRODUCTIVE_PLAN_REJECTED'}};
      }
    });
    return {plan:{execution_id:executionId,actions:actions.map(function(p){return p.step;}),invariants_checked:true,
      valid:!violations.length,block_reason:violations.length ? violations[0].detail : null},
      violations:violations,per_action:per,composite_effects:[],requires_andres:!!violations.length};
  }
  return {TOOLS:TOOLS,config:config,enabled:enabled,destination:destination,assertObject:assertObject,
    assertAncestry:assertAncestry,normalizeId:normalizeId,exact:exact,fail:fail,contract:contract,invoke:invoke,
    check:check,validatePayload:validatePayload,validatePlan:validatePlan,
    _useConfig:function(c){_override=c;}};
})();
