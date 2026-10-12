/** Symbolic property names only; secret values never belong in source. */
var Config = (function () {
  var SECRET_PROPERTY_NAMES = { HEALTH_OS_READ_KEY: 'METIS_HEALTH_OS_READ_KEY' };
  var SETTING_PROPERTY_NAMES = { HEALTH_OS_BASE_URL: 'METIS_HEALTH_OS_BASE_URL' };

  function read(name) { return PropertiesService.getScriptProperties().getProperty(name); }
  function secret(symbol) {
    var value = read(SECRET_PROPERTY_NAMES[symbol]);
    if (!value) { throw new Error('Credencial Health OS ausente'); }
    return value;
  }
  function setting(symbol) { return read(SETTING_PROPERTY_NAMES[symbol]); }
  function hasSecret(symbol) { return !!read(SECRET_PROPERTY_NAMES[symbol]); }

  return { secret: secret, setting: setting, hasSecret: hasSecret };
})();
