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
  function replace(files, name, source) {
    const f = files.find(x => x.name === name || x.name.endsWith('/'+name));
    if (!f) throw new Error('Missing '+name);
    f.source = source;
  }
  const engine = await api('/'+engineId+'/content');
  const engineSourceDir = path.join(root,'metis-orchestrator-prototype','src');
  const engineSources = fs.readdirSync(engineSourceDir).filter(name => name.endsWith('.gs'));
  for (const fileName of engineSources) {
    replace(engine.files, path.basename(fileName,'.gs'), fs.readFileSync(path.join(engineSourceDir,fileName),'utf8'));
  }
  await api('/'+engineId+'/content','PUT',{files:engine.files});
  const engineVersion = await api('/'+engineId+'/versions','POST',{description:'Engine v9; full source synchronization with bounded empty-output recovery'});

  const bridge = await api('/'+bridgeId+'/content');
  replace(bridge.files, 'Bridge', fs.readFileSync(path.join(root,'metis-gateway-bridge','Bridge.gs'),'utf8'));
  const manifest = bridge.files.find(x => x.name === 'appsscript' || x.name.endsWith('/appsscript'));
  if (!manifest) throw new Error('Missing bridge manifest');
  const manifestJson = JSON.parse(manifest.source);
  const library = manifestJson.dependencies.libraries.find(x => x.userSymbol === 'Engine');
  if (!library || library.libraryId !== engineId) throw new Error('Unexpected engine library');
  library.version = String(engineVersion.versionNumber);
  library.developmentMode = false;
  manifest.source = JSON.stringify(manifestJson, null, 2);
  await api('/'+bridgeId+'/content','PUT',{files:bridge.files});
  const bridgeVersion = await api('/'+bridgeId+'/versions','POST',{description:'Gateway durable execution; one paid provider call per invocation'});
  const deployment = await api('/'+bridgeId+'/deployments/'+deploymentId);
  await api('/'+bridgeId+'/deployments/'+deploymentId,'PUT',{deploymentConfig:{
    ...deployment.deploymentConfig, versionNumber:bridgeVersion.versionNumber
  }});

  const verifyEngine = await api('/'+engineId+'/content');
  const verifyBridge = await api('/'+bridgeId+'/content');
  for (const fileName of engineSources) {
    const sourceName = path.basename(fileName,'.gs');
    const remote = verifyEngine.files.find(x=>x.name===sourceName||x.name.endsWith('/'+sourceName));
    const local = fs.readFileSync(path.join(engineSourceDir,fileName),'utf8').trim();
    if (!remote || remote.source.trim() !== local) throw new Error('Engine verification failed for '+sourceName);
  }
  if (verifyBridge.files.find(x=>x.name==='Bridge'||x.name.endsWith('/Bridge')).source.trim() !== fs.readFileSync(path.join(root,'metis-gateway-bridge','Bridge.gs'),'utf8').trim()) throw new Error('Bridge verification failed');
  console.log(JSON.stringify({engine_version:engineVersion.versionNumber,bridge_version:bridgeVersion.versionNumber,deployment_id:deploymentId,verified:true}));
}
main().catch(e=>{console.error(e.message);process.exitCode=1;});
