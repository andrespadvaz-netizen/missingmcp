/**
 * Config.gs — única fuente de tunables del prototipo Nivel 0–2.
 *
 * Reglas de esta capa (spec §12):
 *   - sólo referencia CLAVES SIMBÓLICAS de Script Properties, nunca valores;
 *   - ningún secreto vive en este archivo, en logs, prompts, handoffs ni ledger.
 *
 * Lo que es POLÍTICA vive aquí con un default declarado (límites de corrida).
 * Lo que es DATO DEL ENTORNO — precios de proveedor, ids de fuentes por
 * contexto — NO tiene default inventado: se externaliza a Script Properties y,
 * si falta, la corrida falla cerrada en vez de estimar o adivinar.
 *
 * Este archivo NO registra triggers y NO expone ninguna operación de escritura.
 */
var Config = (function () {
  /** Niveles de sandbox autorizados para este prototipo (spec §2, diseño §3). */
  var LEVELS = {
    LEVEL_0: 'LEVEL_0', // simulación pura: Retrieval servido por fixtures
    LEVEL_1: 'LEVEL_1', // lectura real de sólo lectura contra fuentes competentes
    LEVEL_2: 'LEVEL_2'  // + construcción/validación de planes; toda escritura simulada
  };

  /**
   * Nivel efectivo de la corrida.
   *
   * >>> ESTADO ACTUAL: LEVEL_0 — RUNTIME INERTE. <<<
   *
   * Este comentario describe el valor de la línea que le sigue y sólo eso. La
   * versión anterior decía "fijado en LEVEL_1" mientras el código asignaba
   * LEVEL_0: quien verificaba el estado leyendo la documentación del archivo
   * obtenía la respuesta contraria a la verdad. Si cambias `RUN_LEVEL`, cambia
   * también esta línea en el mismo commit.
   *
   * En LEVEL_0 los cuatro adaptadores de lectura real están bloqueados por
   * código (`Errors.levelViolation`) aunque haya credenciales cargadas: es el
   * estado seguro por defecto y el único en el que debe quedar el proyecto
   * fuera de un ensayo autorizado.
   *
   * Para un ensayo real de sólo lectura se sube a LEVEL_1 y se devuelve a
   * LEVEL_0 al terminar, en la misma sesión. LEVEL_1 no habilita escritura: la
   * escritura no existe en este prototipo, y LEVEL_2 sólo añade construcción y
   * validación de planes simulados.
   */
  var RUN_LEVEL = LEVELS.LEVEL_0;

  /**
   * Nombres de Script Properties. VALORES NUNCA AQUÍ.
   * El modelo sólo ve el nombre abstracto de capacidad (`capability`), jamás
   * el nombre de la propiedad ni su contenido.
   */
  var SECRET_PROPERTY_NAMES = {
    OPENAI_API_KEY: 'METIS_OPENAI_API_KEY',
    ANTHROPIC_API_KEY: 'METIS_ANTHROPIC_API_KEY',
    NOTION_API_KEY: 'METIS_NOTION_API_KEY',
    ASANA_API_KEY: 'METIS_ASANA_API_KEY'
  };

  /** Parámetros no secretos, también en Script Properties. */
  var SETTING_PROPERTY_NAMES = {
    SOURCE_PARTITIONS: 'METIS_SOURCE_PARTITIONS',
    PRICING: 'METIS_PRICING',
    LIMITS: 'METIS_LIMITS',
    DECISIONS_DATA_SOURCE_ID: 'METIS_DECISIONS_DATA_SOURCE_ID'
  };

  /** Capacidades abstractas visibles para los modelos (spec §12). */
  var CAPABILITIES = {
    NOTION_READ: 'notion_read',
    ASANA_READ: 'asana_read',
    DRIVE_READ: 'drive_read',
    CALENDAR_READ: 'calendar_read'
  };

  var PROVIDERS = {
    OPENAI: {
      name: 'OPENAI',
      endpoint: 'https://api.openai.com/v1/responses',
      model: 'gpt-5',
      secret_key: 'OPENAI_API_KEY'
    },
    ANTHROPIC: {
      name: 'ANTHROPIC',
      endpoint: 'https://api.anthropic.com/v1/messages',
      model: 'claude-opus-5',
      version_header: '2023-06-01',
      secret_key: 'ANTHROPIC_API_KEY',
      max_tokens: 4096
    }
  };

  /**
   * Versión de la API de Notion. `2025-09-03` es la que expone data sources y
   * el endpoint `/v1/data_sources/{id}/query`, que es la vía competente para
   * leer Decisiones Tomadas con su filtro por Proyecto y sus relaciones.
   */
  var NOTION_VERSION = '2025-09-03';

  /**
   * Contextos de Metis. `primary` orienta continuidad pero NO obliga handoff
   * (spec §8). `signals` son marcadores léxicos usados SOLO para detectar
   * candidatos antes de recuperar sustancia (spec §6.3, fase de lectura previa).
   * `notion_project` es el valor EXACTO de la propiedad select `Proyecto` en la
   * database Decisiones Tomadas; es la clave de partición real de esa fuente.
   */
  var CONTEXTS = {
    METIS:               { primary: 'ANTHROPIC', notion_project: 'Metis',
                           signals: ['metis', 'canon', 'ciclo 1', 'ciclo 2', 'gate', 'orquestacion', 'phi'] },
    ANDREA:              { primary: 'OPENAI',    notion_project: 'Andrea',
                           signals: ['andrea', 'comercial', 'armando'] },
    SHOKKO:              { primary: 'ANTHROPIC', notion_project: 'Shokko',
                           signals: ['shokko'] },
    VENTURE_QUEST:       { primary: 'ANTHROPIC', notion_project: 'Venture Quest',
                           signals: ['venture quest', 'venture'] },
    ARQUITECTO_INTERIOR: { primary: 'ANTHROPIC', notion_project: 'Arquitecto Interior',
                           signals: ['arquitecto interior'] },
    PERSONAL:            { primary: 'ANTHROPIC', notion_project: 'Personal',
                           signals: ['sistema personal', 'personal'] },
    FINAL_FINAL:         { primary: 'OPENAI',    notion_project: '.Final_Final',
                           signals: ['.final_final', 'final final'] }
  };

  /**
   * Superficies prohibidas para cualquier acción material, incluso simulada
   * (spec §7.4 y §19).
   */
  var FORBIDDEN_SURFACES = [
    'CANON',
    'CANON_CORE',
    'DECISIONES_TOMADAS',
    'CHAT_LOG',
    'HANDOFFS_PHI',
    'GMAIL',
    'CALENDAR_PRIMARY',
    'RITUAL_INSTANCE'
  ];

  /** Campos cuya modificación está vedada al prototipo (spec §7.3). */
  var PROTECTED_DATE_FIELDS = [
    'evaluation_date',
    'fecha_evaluacion',
    'expires_at',
    'caducidad',
    'ritual_trigger',
    'disparador_ritual',
    'next_evaluation'
  ];

  /** Efectos prohibidos por política fija; viajan en cada AuthorityGrant. */
  var FORBIDDEN_EFFECTS = [
    'REAL_EXTERNAL_WRITE',
    'CROSS_CONTEXT_SUBSTANCE_TRANSFER',
    'HIDE_OVERDUE_RITUAL',
    'SCHEDULE_SHIFT_WITHOUT_OPERATOR',
    'MUTATE_PRODUCTIVE_SOURCE'
  ];

  /**
   * Límites por corrida y agregados (spec §13). Son POLÍTICA: llevan default
   * declarado y se pueden sobreescribir con la Script Property `METIS_LIMITS`
   * (JSON parcial). Alcanzarlos = parada dura, nunca auto-ampliación.
   */
  var DEFAULT_LIMITS = {
    // Un ciclo cerrado: productor (lectura + producción) + auditor (lectura +
    // veredicto). Cuatro intervenciones, sin bucles adicionales.
    MAX_MODEL_INTERVENTIONS: 4,
    MAX_TOOL_CALLS: 16,
    MAX_READ_RETRIES: 2
  };

  /**
   * Los techos monetarios NO tienen default. Cuánto está dispuesto a gastar el
   * operador no es una decisión que pueda tomar el código: un default plausible
   * se convierte en el presupuesto real de todo el mundo sin que nadie lo haya
   * decidido. Se declaran en `METIS_LIMITS`; sin ellos, `limits()` los devuelve
   * `null` y ningún proveedor real acepta una llamada.
   */
  var REQUIRED_BUDGET_KEYS = ['MAX_RUN_BUDGET_USD', 'MAX_DAILY_BUDGET_USD', 'MAX_MONTHLY_BUDGET_USD'];

  var _limitsOverride = null;

  /** Límites efectivos: defaults de política + techos monetarios externos. */
  function limits() {
    var effective = {};
    Object.keys(DEFAULT_LIMITS).forEach(function (k) { effective[k] = DEFAULT_LIMITS[k]; });
    REQUIRED_BUDGET_KEYS.forEach(function (k) { effective[k] = null; });

    var override = _limitsOverride !== null ? _limitsOverride : _readJson('LIMITS');
    if (override) {
      Object.keys(override).forEach(function (k) {
        if (Object.prototype.hasOwnProperty.call(effective, k) && typeof override[k] === 'number') {
          effective[k] = override[k];
        }
      });
    }
    return effective;
  }

  /** Techos monetarios que faltan por declarar. */
  function missingBudgets() {
    var effective = limits();
    return REQUIRED_BUDGET_KEYS.filter(function (k) { return typeof effective[k] !== 'number'; });
  }

  /**
   * Puerta previa a CUALQUIER llamada a un proveedor real. Sin techo declarado
   * no se gasta: no hay contra qué comparar el gasto acumulado, y un límite que
   * no existe no puede detener nada.
   */
  function assertBudgetsConfigured() {
    var missing = missingBudgets();
    if (missing.length) { throw Errors.budgetUnconfigured(missing); }
    return true;
  }

  /** Handoff: TTL corto; replay y caducidad se rechazan visiblemente (§15). */
  var HANDOFF_TTL_MS = 15 * 60 * 1000;

  /** Ledger técnico: retención corta y configurable (spec §5). */
  var LEDGER = {
    PROPERTY_PREFIX: 'METIS_LEDGER_',
    HANDOFF_PREFIX: 'METIS_HANDOFF_',
    RETENTION_MS: 24 * 60 * 60 * 1000,
    COUNTER_PREFIX: 'METIS_BUDGET_'
  };

  /**
   * Clasificación de competencia de fuentes (spec §9).
   * CANON manda en autoridad/reglas/protocolos.
   * Registro de decisiones + fuente operativa mandan en estado y vigencia.
   */
  var SOURCE_COMPETENCE = {
    AUTHORITY: ['CANON', 'CANON_CORE'],
    OPERATIONAL_STATE: ['DECISION_REGISTRY', 'ASANA', 'CALENDAR', 'DRIVE', 'NOTION_OPERATIONAL']
  };

  // -------------------------------------------------- partición por contexto
  var _partitionsOverride = null;

  /**
   * Partición de fuentes por contexto (spec §6.3: lectura acotada al contexto).
   * Los ids son del entorno, no del código: se declaran en la Script Property
   * `METIS_SOURCE_PARTITIONS`. Forma:
   *
   *   { "METIS": {
   *       "notion":   { "data_sources": ["<uuid>"], "project": "Metis" },
   *       "asana":    { "project_gids": ["..."] },
   *       "drive":    { "folder_ids": ["..."] },
   *       "calendar": { "calendar_ids": ["..."] } }, ... }
   *
   * Sin partición declarada para (contexto, fuente) NO se lee esa fuente.
   */
  function partitionFor(context, source) {
    var all = _partitionsOverride !== null ? _partitionsOverride : _readJson('SOURCE_PARTITIONS');
    if (!all || !all[context]) { return null; }
    var key = String(source).toLowerCase();
    return all[context][key] ? all[context][key] : null;
  }

  function declaredPartitionContexts() {
    var all = _partitionsOverride !== null ? _partitionsOverride : _readJson('SOURCE_PARTITIONS');
    return all ? Object.keys(all) : [];
  }

  // ------------------------------------------------------------- precios
  /**
   * Precio por 1.000 tokens, por proveedor y modelo. NO hay default: los
   * precios cambian y un número inventado produce un contador de costo falso.
   * Se declaran en `METIS_PRICING`:
   *
   *   { "OPENAI": { "gpt-5": { "input_per_1k": 0.0, "output_per_1k": 0.0 } },
   *     "ANTHROPIC": { "claude-opus-5": { ... } } }
   *
   * La clave del modelo debe ser EXACTAMENTE la que el proveedor devuelve en
   * su respuesta, que no siempre coincide con la que se pidió: algunos
   * proveedores responden con una instantánea fechada. No hay coincidencia por
   * prefijo a propósito. Si mañana existe un identificador parecido con tarifa
   * distinta, el sistema debe fallar en vez de suponer que "se parece".
   *
   * Sin precio para el modelo usado, el costo queda DESCONOCIDO y el
   * orquestador falla cerrado en vez de estimar.
   */
  var _pricingOverride = null;

  function priceFor(provider, model) {
    var pricing = _pricingOverride !== null ? _pricingOverride : _readJson('PRICING');
    if (!pricing || !pricing[provider] || !pricing[provider][model]) { return null; }
    var entry = pricing[provider][model];
    if (typeof entry.input_per_1k !== 'number' || typeof entry.output_per_1k !== 'number') { return null; }
    return { input_per_1k: entry.input_per_1k, output_per_1k: entry.output_per_1k };
  }

  // -------------------------------------------------------------- helpers
  function levelAllowsRealReads(level) {
    var l = level || RUN_LEVEL;
    return l === LEVELS.LEVEL_1 || l === LEVELS.LEVEL_2;
  }

  /**
   * ¿Este nivel permite invocar modelos reales? Sólo LEVEL_2.
   *
   * Existe porque faltaba: los cuatro adaptadores de lectura lanzan violación
   * de nivel en LEVEL_0, pero los de proveedor no comprobaban el nivel en
   * absoluto. Con las credenciales cargadas, eso significaba que el runtime
   * "inerte" no leía nada y aun así podía gastar dinero. `LEVEL_0` debe
   * significar que no ocurre NINGUNA llamada externa, no sólo que no se lee.
   *
   * La separación entre niveles queda así: LEVEL_1 lee fuentes reales y no
   * gasta; LEVEL_2 añade la invocación de modelos, que es la única operación
   * de este prototipo con coste monetario. Subir a LEVEL_2 es por tanto una
   * decisión del operador con consecuencia económica, distinta de leer.
   */
  function levelAllowsModelCalls(level) {
    var l = level || RUN_LEVEL;
    return l === LEVELS.LEVEL_2;
  }

  function levelAllowsPlanConstruction(level) {
    var l = level || RUN_LEVEL;
    return l === LEVELS.LEVEL_2 || l === LEVELS.LEVEL_0;
  }

  function contextNames() {
    return Object.keys(CONTEXTS);
  }

  function primaryFor(context) {
    return CONTEXTS[context] ? CONTEXTS[context].primary : null;
  }

  function notionProjectFor(context) {
    return CONTEXTS[context] ? CONTEXTS[context].notion_project : null;
  }

  /**
   * Lee una Script Property por CLAVE SIMBÓLICA. Devuelve el valor sólo al
   * llamador interno (adaptadores); nunca se registra ni se devuelve al modelo.
   */
  function secret(symbolicKey) {
    var propName = SECRET_PROPERTY_NAMES[symbolicKey];
    if (!propName) {
      throw Errors.configError('Clave simbólica desconocida: ' + symbolicKey);
    }
    var value = _readProperty(propName);
    if (!value) {
      throw Errors.missingCredential(symbolicKey);
    }
    return value;
  }

  function setting(symbolicKey) {
    var propName = SETTING_PROPERTY_NAMES[symbolicKey];
    if (!propName) {
      throw Errors.configError('Ajuste desconocido: ' + symbolicKey);
    }
    return _readProperty(propName);
  }

  function hasSecret(symbolicKey) {
    var propName = SECRET_PROPERTY_NAMES[symbolicKey];
    if (!propName) { return false; }
    return !!_readProperty(propName);
  }

  function _readJson(symbolicKey) {
    var raw = setting(symbolicKey);
    if (!raw) { return null; }
    try {
      return JSON.parse(raw);
    } catch (e) {
      throw Errors.configError('Script Property ' + SETTING_PROPERTY_NAMES[symbolicKey] + ' no es JSON válido');
    }
  }

  function _readProperty(name) {
    if (typeof PropertiesService === 'undefined') { return null; }
    return PropertiesService.getScriptProperties().getProperty(name);
  }

  /** Overrides sólo para pruebas locales; una corrida real usa Script Properties. */
  function _setRunLevel(level) {
    if (!LEVELS[level]) { throw Errors.configError('Nivel inválido: ' + level); }
    RUN_LEVEL = level;
  }
  function _setPartitions(map) { _partitionsOverride = map; }
  function _setLimits(map) { _limitsOverride = map; }
  /** Sólo para tests: sustituye la tabla de precios sin tocar Script Properties. */
  function _setPricing(map) { _pricingOverride = map; }

  function runLevel() { return RUN_LEVEL; }

  return {
    LEVELS: LEVELS,
    runLevel: runLevel,
    _setRunLevel: _setRunLevel,
    _setPartitions: _setPartitions,
    _setLimits: _setLimits,
    _setPricing: _setPricing,
    SECRET_PROPERTY_NAMES: SECRET_PROPERTY_NAMES,
    SETTING_PROPERTY_NAMES: SETTING_PROPERTY_NAMES,
    CAPABILITIES: CAPABILITIES,
    PROVIDERS: PROVIDERS,
    NOTION_VERSION: NOTION_VERSION,
    CONTEXTS: CONTEXTS,
    FORBIDDEN_SURFACES: FORBIDDEN_SURFACES,
    PROTECTED_DATE_FIELDS: PROTECTED_DATE_FIELDS,
    FORBIDDEN_EFFECTS: FORBIDDEN_EFFECTS,
    DEFAULT_LIMITS: DEFAULT_LIMITS,
    REQUIRED_BUDGET_KEYS: REQUIRED_BUDGET_KEYS,
    limits: limits,
    missingBudgets: missingBudgets,
    assertBudgetsConfigured: assertBudgetsConfigured,
    HANDOFF_TTL_MS: HANDOFF_TTL_MS,
    LEDGER: LEDGER,
    SOURCE_COMPETENCE: SOURCE_COMPETENCE,
    partitionFor: partitionFor,
    declaredPartitionContexts: declaredPartitionContexts,
    priceFor: priceFor,
    levelAllowsRealReads: levelAllowsRealReads,
    levelAllowsModelCalls: levelAllowsModelCalls,
    levelAllowsPlanConstruction: levelAllowsPlanConstruction,
    contextNames: contextNames,
    primaryFor: primaryFor,
    notionProjectFor: notionProjectFor,
    secret: secret,
    setting: setting,
    hasSecret: hasSecret
  };
})();
