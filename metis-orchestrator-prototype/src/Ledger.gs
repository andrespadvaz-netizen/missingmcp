/**
 * Ledger.gs — ledger técnico mínimo (spec §4.7, §5, §13).
 *
 * Propiedades no negociables:
 *   - identidad de acción = `execution_id + ":" + ordinal`;
 *   - la huella del payload NO define identidad: es guarda de consistencia;
 *     mismo ordinal con payload distinto => FAILED, la corrida no continúa;
 *   - NO contiene sustancia de negocio ni secretos: sólo ids, hashes,
 *     timestamps, estado y rol técnico;
 *   - ningún modelo lo consulta para responder sobre el estado de Metis;
 *   - sin ledger disponible, toda acción material simulada se bloquea
 *     (fail closed).
 *
 * Retención corta y configurable: `Config.LEDGER.RETENTION_MS`.
 */
var Ledger = (function () {
  /** Máquina de estados por acción (spec §5). */
  var TRANSITIONS = {
    INTENT_RECORDED: ['SENT', 'FAILED'],
    SENT: ['CONFIRMED', 'FAILED', 'UNCERTAIN', 'SIMULATED'],
    CONFIRMED: [],
    FAILED: [],
    UNCERTAIN: [],
    SIMULATED: []
  };

  /** Campos con texto libre admitidos; el resto son ids/hashes/enums. */
  var TOOL_PATTERN = /^[a-z0-9_.]{1,64}$/;

  // ------------------------------------------------------------------ store
  function propertiesStore() {
    return {
      available: function () {
        try {
          if (typeof PropertiesService === 'undefined') { return false; }
          PropertiesService.getScriptProperties().getProperty('__probe__');
          return true;
        } catch (e) {
          return false;
        }
      },
      get: function (key) { return PropertiesService.getScriptProperties().getProperty(key); },
      set: function (key, value) { PropertiesService.getScriptProperties().setProperty(key, value); },
      remove: function (key) { PropertiesService.getScriptProperties().deleteProperty(key); },
      keys: function () { return PropertiesService.getScriptProperties().getKeys(); }
    };
  }

  /** Store en memoria: usado por los tests y por Nivel 0. */
  function memoryStore(initiallyAvailable) {
    var data = {};
    var up = (initiallyAvailable === undefined) ? true : !!initiallyAvailable;
    return {
      available: function () { return up; },
      setAvailable: function (v) { up = !!v; },
      get: function (key) { return Object.prototype.hasOwnProperty.call(data, key) ? data[key] : null; },
      set: function (key, value) {
        if (!up) { throw Errors.ledgerUnavailable('store caído'); }
        data[key] = value;
      },
      remove: function (key) { delete data[key]; },
      keys: function () { return Object.keys(data); },
      _dump: function () { return data; }
    };
  }

  var _store = null;

  function useStore(store) { _store = store; }
  function store() {
    if (!_store) { _store = propertiesStore(); }
    return _store;
  }
  function reset() { _store = null; }

  function available() {
    try {
      return store().available();
    } catch (e) {
      return false;
    }
  }

  function _key(executionId, ordinal) {
    return Config.LEDGER.PROPERTY_PREFIX + executionId + ':' + ordinal;
  }

  function _read(executionId, ordinal) {
    var raw = store().get(_key(executionId, ordinal));
    return raw ? JSON.parse(raw) : null;
  }

  function _write(entry) {
    Schemas.assertValid('LedgerEntry', entry);
    assertNoSubstance(entry);
    store().set(_key(entry.execution_id, entry.ordinal), JSON.stringify(entry));
    return entry;
  }

  /**
   * Verifica que la entrada no transporte sustancia de negocio ni secretos.
   * El conjunto de campos ya es exacto (Schemas); aquí se acota además el
   * contenido de los campos de texto libre.
   */
  function assertNoSubstance(entry) {
    if (!TOOL_PATTERN.test(String(entry.tool))) {
      throw Errors.make(Errors.CODES.LEDGER_SUBSTANCE, 'Ledger: `tool` fuera de forma admitida');
    }
    if (!TOOL_PATTERN.test(String(entry.operation))) {
      throw Errors.make(Errors.CODES.LEDGER_SUBSTANCE, 'Ledger: `operation` fuera de forma admitida');
    }
    if (entry.provider_object_id !== null && String(entry.provider_object_id).length > 96) {
      throw Errors.make(Errors.CODES.LEDGER_SUBSTANCE, 'Ledger: provider_object_id demasiado largo');
    }
    return true;
  }

  // ------------------------------------------------------------------- API
  /**
   * Registra la intención de una acción. Idempotente por (execution_id, ordinal).
   * Si el mismo ordinal reaparece con otra huella de payload, la acción se marca
   * FAILED y se lanza: la corrida no continúa (spec §5).
   */
  function recordIntent(input) {
    if (!available()) {
      throw Errors.ledgerUnavailable('no se puede registrar intención');
    }
    var existing = _read(input.execution_id, input.ordinal);
    if (existing) {
      if (existing.payload_hash !== input.payload_hash) {
        existing.status = 'FAILED';
        existing.timestamp = Schemas.nowIso();
        _write(existing);
        throw Errors.ledgerConsistency(existing.action_id);
      }
      return existing;
    }
    var entry = {
      execution_id: input.execution_id,
      ordinal: input.ordinal,
      action_id: Schemas.actionId(input.execution_id, input.ordinal),
      payload_hash: input.payload_hash,
      tool: input.tool,
      operation: input.operation,
      destination_hash: (input.destination_hash === undefined ? null : input.destination_hash),
      status: 'INTENT_RECORDED',
      provider_object_id: null,
      model_role: input.model_role,
      timestamp: Schemas.nowIso()
    };
    return _write(entry);
  }

  function transition(executionId, ordinal, nextStatus, providerObjectId) {
    if (!available()) {
      throw Errors.ledgerUnavailable('no se puede transicionar acción');
    }
    var entry = _read(executionId, ordinal);
    if (!entry) {
      throw Errors.make(Errors.CODES.LEDGER_CONSISTENCY, 'Transición sobre acción no registrada: ' + Schemas.actionId(executionId, ordinal));
    }
    var allowed = TRANSITIONS[entry.status] || [];
    if (allowed.indexOf(nextStatus) === -1) {
      throw Errors.make(Errors.CODES.LEDGER_CONSISTENCY,
        'Transición inválida ' + entry.status + ' -> ' + nextStatus + ' en ' + entry.action_id);
    }
    entry.status = nextStatus;
    if (providerObjectId !== undefined && providerObjectId !== null) {
      entry.provider_object_id = String(providerObjectId);
    }
    entry.timestamp = Schemas.nowIso();
    return _write(entry);
  }

  function get(executionId, ordinal) { return _read(executionId, ordinal); }

  function entriesFor(executionId) {
    var prefix = Config.LEDGER.PROPERTY_PREFIX + executionId + ':';
    var keys = store().keys();
    var out = [];
    for (var i = 0; i < keys.length; i++) {
      if (keys[i].indexOf(prefix) === 0) {
        out.push(JSON.parse(store().get(keys[i])));
      }
    }
    out.sort(function (a, b) { return a.ordinal - b.ordinal; });
    return out;
  }

  /** Retención corta: poda por antigüedad (spec §5). */
  function purge(retentionMs) {
    var cutoff = Schemas.nowMs() - (retentionMs === undefined ? Config.LEDGER.RETENTION_MS : retentionMs);
    var keys = store().keys();
    var removed = 0;
    for (var i = 0; i < keys.length; i++) {
      if (keys[i].indexOf(Config.LEDGER.PROPERTY_PREFIX) !== 0) { continue; }
      var entry = JSON.parse(store().get(keys[i]));
      if (new Date(entry.timestamp).getTime() < cutoff) {
        store().remove(keys[i]);
        removed++;
      }
    }
    return removed;
  }

  // --------------------------------------------------- contadores de costo
  function _counterKey(scope) {
    var d = Schemas.now();
    var y = d.getUTCFullYear();
    var m = ('0' + (d.getUTCMonth() + 1)).slice(-2);
    var day = ('0' + d.getUTCDate()).slice(-2);
    return Config.LEDGER.COUNTER_PREFIX + scope + '_' + (scope === 'DAILY' ? (y + m + day) : (y + m));
  }

  function spend(scope) {
    var raw = store().get(_counterKey(scope));
    return raw ? Number(raw) : 0;
  }

  function addSpend(usd) {
    ['DAILY', 'MONTHLY'].forEach(function (scope) {
      var key = _counterKey(scope);
      var current = store().get(key);
      store().set(key, String((current ? Number(current) : 0) + usd));
    });
  }

  /** Parada dura al alcanzar cualquier techo agregado (spec §13). */
  function assertAggregateBudget() {
    if (spend('DAILY') >= Config.LIMITS.MAX_DAILY_BUDGET_USD) {
      throw Errors.limitExceeded('MAX_DAILY_BUDGET_USD', spend('DAILY'));
    }
    if (spend('MONTHLY') >= Config.LIMITS.MAX_MONTHLY_BUDGET_USD) {
      throw Errors.limitExceeded('MAX_MONTHLY_BUDGET_USD', spend('MONTHLY'));
    }
    return true;
  }

  return {
    TRANSITIONS: TRANSITIONS,
    propertiesStore: propertiesStore,
    memoryStore: memoryStore,
    useStore: useStore,
    reset: reset,
    available: available,
    recordIntent: recordIntent,
    transition: transition,
    get: get,
    entriesFor: entriesFor,
    purge: purge,
    assertNoSubstance: assertNoSubstance,
    spend: spend,
    addSpend: addSpend,
    assertAggregateBudget: assertAggregateBudget
  };
})();
