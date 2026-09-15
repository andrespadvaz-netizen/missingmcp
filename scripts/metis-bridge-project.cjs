// Prepare/update ONLY the separate bridge project. Never modify the engine.
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const root = path.resolve(__dirname,'..');
const statePath = path.join(root,'.localdata','metis-bridge-project.json');
const engine = '1g9BKx5-TShUuUi2w0fZLByFydG8_l1WB74vn-MDOYWc4nd3OGiolDp9o';
async function main() {
  const credentials = JSON.parse(fs.readFileSync(path.join(os.homedir(),'.clasprc.json'),'utf8')).tokens.default;
  const refresh = await fetch('https://oauth2.googleapis.com/token', {method:'POST',
    body:new URLSearchParams({client_id:credentials.client_id,client_secret:credentials.client_secret,
      refresh_token:credentials.refresh_token,grant_type:'refresh_token'})});
  const token = await refresh.json();
  if(!refresh.ok) throw new Error('Authorization refresh failed: '+refresh.status);
  async function api(suffix,method='GET',body) {
    const r=await fetch('https://script.googleapis.com/v1/projects'+suffix,{method,
      headers:{Authorization:'Bearer '+token.access_token,'Content-Type':'application/json'},
      body:body?JSON.stringify(body):undefined});
    const data=await r.json();
    if(!r.ok) throw new Error('Apps Script API '+r.status+': '+(data.error?.status||'failed'));
    return data;
  }
  let state=fs.existsSync(statePath)?JSON.parse(fs.readFileSync(statePath)):null;
  if(!state) {
    state=await api('','POST',{title:'Metis Orchestration Gateway v1 — bridge'});
    fs.mkdirSync(path.dirname(statePath),{recursive:true});
    fs.writeFileSync(statePath,JSON.stringify(state,null,2));
  }
  if(state.scriptId===engine) throw new Error('Refusing to modify baseline engine');
  const files=[{name:'appsscript',type:'JSON',source:fs.readFileSync(path.join(root,'metis-gateway-bridge','appsscript.json'),'utf8')},
    {name:'Bridge',type:'SERVER_JS',source:fs.readFileSync(path.join(root,'metis-gateway-bridge','Bridge.gs'),'utf8')},
    {name:'WriteBridge',type:'SERVER_JS',source:fs.readFileSync(path.join(root,'metis-gateway-bridge','WriteBridge.gs'),'utf8')},
    {name:'Diagnostics',type:'SERVER_JS',source:fs.readFileSync(path.join(root,'metis-gateway-bridge','Diagnostics.gs'),'utf8')},
    {name:'ConnectionCopy',type:'SERVER_JS',source:fs.readFileSync(path.join(root,'metis-gateway-bridge','ConnectionCopy.gs'),'utf8')}];
  await api('/'+state.scriptId+'/content','PUT',{files});
  const check=await api('/'+state.scriptId+'/content');
  for(const file of files) {
    if(check.files.find(f=>f.name===file.name)?.source.trim()!==file.source.trim()) throw new Error('Content verification failed');
  }
  if(process.argv.includes('--update-deployment')) {
    if(!state.deployment?.deploymentId) throw new Error('No existing deployment');
    const current=await api('/'+state.scriptId+'/deployments/'+state.deployment.deploymentId);
    const version=await api('/'+state.scriptId+'/versions','POST',{description:'Productive write receipts; reviewed engine library v7'});
    state.deployment=await api('/'+state.scriptId+'/deployments/'+state.deployment.deploymentId,'PUT',{
      deploymentConfig:{...current.deploymentConfig,versionNumber:version.versionNumber}});
    state.versionNumber=version.versionNumber;
    fs.writeFileSync(statePath,JSON.stringify(state,null,2));
  }
  if(process.argv.includes('--deploy')) {
    if(state.deployment) throw new Error('Deployment already recorded; inspect before updating');
    const version=await api('/'+state.scriptId+'/versions','POST',{description:'Gateway v1 transport candidate; frozen engine library v5'});
    state.versionNumber=version.versionNumber;
    fs.writeFileSync(statePath,JSON.stringify(state,null,2));
    state.deployment=await api('/'+state.scriptId+'/deployments','POST',{
      versionNumber:version.versionNumber,manifestFileName:'appsscript',
      description:'Metis Gateway v1 bridge — HMAC authenticated; engine v5'});
    fs.writeFileSync(statePath,JSON.stringify(state,null,2));
  }
  console.log(JSON.stringify({scriptId:state.scriptId,files_verified:files.length,
    editor:'https://script.google.com/home/projects/'+state.scriptId+'/edit',
    deployed:!!state.deployment, deployment:state.deployment}));
}
main().catch(e=>{console.error(e.message);process.exitCode=1;});
