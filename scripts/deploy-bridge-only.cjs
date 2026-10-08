const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');

const root = path.resolve(__dirname, '..');
const engineId = '1g9BKx5-TShUuUi2w0fZLByFydG8_l1WB74vn-MDOYWc4nd3OGiolDp9o';
const bridgeId = '1kO_QM5HgBgG10O1wCTIuqeHgB_GfFRrPXyNnGf4_xr6LrzNsuZbchMBZ';
const deploymentId = 'AKfycbzFapjceF2ElGaY9Vfwj4L0M8Py8ywYS3ZEipXv20cVtt8EajuD-ctXkKxqoRHHwpND';

async function token() {
  const c = JSON.parse(fs.readFileSync(path.join(os.homedir(), '.clasprc.json'), 'utf8')).tokens.default;
  const r = await fetch('https://oauth2.googleapis.com/token', {method:'POST', body:new URLSearchParams({
    client_id:c.client_id, client_secret:c.client_secret, refresh_token:c.refresh_token, grant_type:'refresh_token'
  })});
  if (!r.ok) throw new Error('OAuth refresh '+r.status);
  return (await r.json()).access_token;
}

async function main() {
  const access = await token();
  async function api(suffix, method='GET', body) {
    const r = await fetch('https://script.googleapis.com/v1/projects'+suffix, {method,
      headers:{Authorization:'Bearer '+access,'Content-Type':'application/json'},
      body:body ? JSON.stringify(body) : undefined});
    const data = await r.json();
    if (!r.ok) throw new Error('Apps Script '+r.status+' '+(data.error?.message || 'failed'));
    return data;
  }
  const bridge = await api('/'+bridgeId+'/content');
  const bridgeFile = bridge.files.find(x => x.name === 'Bridge' || x.name.endsWith('/Bridge'));
  if (!bridgeFile) throw new Error('Missing Bridge');
  bridgeFile.source = fs.readFileSync(path.join(root,'metis-gateway-bridge','Bridge.gs'),'utf8');
  const manifest = bridge.files.find(x => x.name === 'appsscript' || x.name.endsWith('/appsscript'));
  if (!manifest) throw new Error('Missing bridge manifest');
  const manifestJson = JSON.parse(manifest.source);
  const library = manifestJson.dependencies.libraries.find(x => x.userSymbol === 'Engine');
  if (!library || library.libraryId !== engineId || ['20','22','26','27','29'].indexOf(String(library.version)) < 0) {
    throw new Error('Bridge is not pinned to an expected rollback engine; found '+String(library && library.version));
  }
  library.version = '30';
  library.developmentMode = false;
  manifest.source = JSON.stringify(manifestJson, null, 2);
  await api('/'+bridgeId+'/content','PUT',{files:bridge.files});
  const bridgeVersion = await api('/'+bridgeId+'/versions','POST',{description:'FINAL_FINAL verified read roots; engine 30'});
  const deployment = await api('/'+bridgeId+'/deployments/'+deploymentId);
  await api('/'+bridgeId+'/deployments/'+deploymentId,'PUT',{deploymentConfig:{
    ...deployment.deploymentConfig, versionNumber:bridgeVersion.versionNumber
  }});
  const verify = await api('/'+bridgeId+'/content');
  const remote = verify.files.find(x => x.name === 'Bridge' || x.name.endsWith('/Bridge'));
  if (!remote || remote.source.trim() !== bridgeFile.source.trim()) throw new Error('Bridge verification failed');
  console.log(JSON.stringify({engine_version:30,bridge_version:bridgeVersion.versionNumber,deployment_id:deploymentId,verified:true}));
}
main().catch(e=>{console.error(e.message);process.exitCode=1;});
