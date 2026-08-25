/**
 * Config.gs — única fuente de tunables del prototipo Nivel 0–2.
 *
 * Reglas de esta capa (spec §12):
 *   - sólo referencia CLAVES SIMBÓLICAS de Script Properties, nunca valores;
 *   - ningún secreto vive en este archivo, en logs, prompts, handoffs ni ledger.
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
   * Nivel efectivo de la corrida. Por defecto LEVEL_0: una instalación recién
   * clonada NO toca red aunque existan credenciales. Subir a LEVEL_1/LEVEL_2 es
   * una acción manual y explícita del operador (README → Activación).
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

  /** Parámetros no secretos, también en Script Properties (ids de espacio, etc.). */
  var SETTING_PROPERTY_NAMES = {
    ASANA_WORKSPACE_GID: 'METIS_ASANA_WORKSPACE_GID',
    CALENDAR_READ_IDS: 'METIS_CALENDAR_READ_IDS',
    DRIVE_READ_FOLDER_IDS: 'METIS_DRIVE_READ_FOLDER_IDS'
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

  var NOTION_VERSION = '2022-06-28';

  /**
   * Contextos de Metis. `primary` orienta continuidad pero NO obliga handoff
   * (spec §8). `signals` son marcadores léxicos usados SOLO para detectar
   * candidatos antes de recuperar sustancia (spec §6.3, fase de lectura previa).
   */
  var CONTEXTS = {
    METIS:               { primary: 'ANTHROPIC', signals: ['metis', 'canon', 'ciclo 1', 'ciclo 2', 'gate', 'orquestacion', 'phi'] },
    ANDREA:              { primary: 'OPENAI',    signals: ['andrea', 'comercial', 'armando'] },
    SHOKKO:              { primary: 'ANTHROPIC', signals: ['shokko'] },
    VENTURE_QUEST:       { primary: 'ANTHROPIC', signals: ['venture quest', 'venture'] },
    ARQUITECTO_INTERIOR: { primary: 'ANTHROPIC', signals: ['arquitecto interior'] },
    SISTEMA_PERSONAL:    { primary: 'ANTHROPIC', signals: ['sistema personal'] },
    FINAL_FINAL:         { primary: 'OPENAI',    signals: ['.final_final', 'final final'] }
  };

  /**
   * Superficies prohibidas para cualquier acción material, incluso simulada
   * (spec §7.4 y §19). Lista negativa dura: se evalúa sobre destino y etiquetas.
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

  /** Límites por corrida y agregados (spec §13). Alcanzarlos = parada dura. */
  var LIMITS = {
    // Un ciclo cerrado: productor (Retrieval + producción) + auditor. Sin bucles.
    MAX_MODEL_INTERVENTIONS: 3,
    MAX_TOOL_CALLS: 12,
    MAX_READ_RETRIES: 2,
    MAX_RUN_BUDGET_USD: 0.50,
    MAX_DAILY_BUDGET_USD: 5.00,
    MAX_MONTHLY_BUDGET_USD: 25.00
  };

  /** Handoff: TTL corto; replay y caducidad se rechazan visiblemente (§15). */
  var HANDOFF_TTL_MS = 15 * 60 * 1000;

  /** Ledger técnico: retención corta y configurable (spec §5). */
  var LEDGER = {
    PROPERTY_PREFIX: 'METIS_LEDGER_',
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

  function levelAllowsRealReads(level) {
    var l = level || RUN_LEVEL;
    return l === LEVELS.LEVEL_1 || l === LEVELS.LEVEL_2;
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

  function _readProperty(name) {
    if (typeof PropertiesService === 'undefined') { return null; }
    return PropertiesService.getScriptProperties().getProperty(name);
  }

  /** Override sólo para pruebas locales; nunca usado en una corrida real. */
  function _setRunLevel(level) {
    if (!LEVELS[level]) { throw Errors.configError('Nivel inválido: ' + level); }
    RUN_LEVEL = level;
  }

  function runLevel() { return RUN_LEVEL; }

  return {
    LEVELS: LEVELS,
    runLevel: runLevel,
    _setRunLevel: _setRunLevel,
    SECRET_PROPERTY_NAMES: SECRET_PROPERTY_NAMES,
    SETTING_PROPERTY_NAMES: SETTING_PROPERTY_NAMES,
    CAPABILITIES: CAPABILITIES,
    PROVIDERS: PROVIDERS,
    NOTION_VERSION: NOTION_VERSION,
    CONTEXTS: CONTEXTS,
    FORBIDDEN_SURFACES: FORBIDDEN_SURFACES,
    PROTECTED_DATE_FIELDS: PROTECTED_DATE_FIELDS,
    FORBIDDEN_EFFECTS: FORBIDDEN_EFFECTS,
    LIMITS: LIMITS,
    HANDOFF_TTL_MS: HANDOFF_TTL_MS,
    LEDGER: LEDGER,
    SOURCE_COMPETENCE: SOURCE_COMPETENCE,
    levelAllowsRealReads: levelAllowsRealReads,
    levelAllowsPlanConstruction: levelAllowsPlanConstruction,
    contextNames: contextNames,
    primaryFor: primaryFor,
    secret: secret,
    setting: setting,
    hasSecret: hasSecret
  };
})();
