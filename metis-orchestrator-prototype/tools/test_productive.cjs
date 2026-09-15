// Execute the real Apps Script policy/actuator with an in-memory provider.
// No Google services or network are available to this harness.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const assert=require('node:assert/strict'),crypto=require('node:crypto');
const props=new Map();
const c=vm.createContext({console,Date,JSON,Math,isFinite,isNaN,encodeURIComponent,
  PropertiesService:{getScriptProperties:()=>({getProperty:k=>props.get(k)||null})},
  Utilities:{getUuid:()=>crypto.randomUUID(),DigestAlgorithm:{SHA_256:1},Charset:{UTF_8:1},
    computeDigest:(_,s)=>Array.from(crypto.createHash('sha256').update(s).digest())}});
const src=path.join(__dirname,'../src');
for(const f of fs.readdirSync(src).filter(f=>f.endsWith('.gs')).sort()) vm.runInContext(fs.readFileSync(path.join(src,f),'utf8'),c,{filename:f});
const {Config,ProductivePolicy:P,ProductiveAdapter:A,Schemas:S,ContextResolver:R}=c;
const clone=x=>JSON.parse(JSON.stringify(x));
const ctx={primary:'ANTHROPIC',notion_project:'Synthetic',signals:['synthetic']};
const policy={enabled:true,version:1,authorization_ref:'synthetic-authorization',revision:'one',protected_ids:['protected'],
  contexts:{TEST:ctx,'TEST/CHILD':{...ctx,signals:['synthetic-child']}},destinations:{
    notes:{provider:'NOTION',context:'TEST',root_id:'root',kind:'routine',operations:['create','update']},
    child:{provider:'NOTION',context:'TEST/CHILD',root_id:'child',kind:'routine',operations:['create','update']},
    tasks:{provider:'ASANA',context:'TEST',root_id:'project',kind:'routine',operations:['create','update']},
    docs:{provider:'DRIVE',context:'TEST',root_id:'folder',kind:'routine',operations:['create','update']}}};
let tests=0;
function test(name,fn){P._useConfig(clone(policy));Config._setRunLevel('LEVEL_3');A._useBackend(null);try{fn();tests++;}catch(e){console.error(name);throw e;}}
function session(context='TEST'){return {scope:{resolved:context},grant:{allowed_tools:P.TOOLS},documents:[],source_status:{},proposed_actions:[]};}
function proposal(alias,id,operation,payload){const s=session();const snap=P.invoke(s,'productive.inspect',{destination:alias,...(id?{object_id:id}:{})});P.invoke(s,'productive.propose',{snapshot_id:snap.snapshot_id,operation,payload});return s.proposed_actions[0].payload;}
test('disabled levels perform no I/O',()=>{let io=0;A._useBackend(()=>{io++;});for(const level of ['LEVEL_0','LEVEL_1','LEVEL_2']){Config._setRunLevel(level);assert.throws(()=>P.invoke(session(),'productive.inspect',{destination:'notes'}));}assert.equal(io,0);});
test('unknown, sibling, parent and protected destinations',()=>{
  assert.throws(()=>P.destination('notes','TEST/CHILD'));assert.throws(()=>P.destination('missing','TEST'));
  assert.throws(()=>P.assertAncestry(policy.destinations.notes,['object','child','root']));
  assert.throws(()=>P.assertAncestry(policy.destinations.notes,['object','foreign']));
  assert.throws(()=>P.assertAncestry(policy.destinations.notes,['protected','root']));
  P.assertAncestry(policy.destinations.child,['object','child','root']);
  assert.equal(R.resolve('Contexto: TEST/CHILD\nUsa datos synthetic',{}).resolved_context,'TEST/CHILD');
  assert.equal(R.resolve('Contexto: UNKNOWN\nsynthetic',{}).resolved_context,null);
  assert.equal(R.resolve('Contexto: Metis. Crea una nota.',{}).resolved_context,'METIS');
  assert.equal(R.resolve('Contexto: .Final_Final. Crea una nota.',{}).resolved_context,'FINAL_FINAL');
  assert.equal(R.resolve('synthetic',{operator_context:'UNKNOWN'}).resolved_context,null);
});
test('no uninspected proposal and no extra destructive fields',()=>{
  assert.throws(()=>P.invoke(session(),'productive.propose',{snapshot_id:'invented',operation:'create',payload:{title:'x',text:'y'}}));
  for(const key of ['archived','parent','url','permissions','assignee']) assert.throws(()=>P.validatePayload('ASANA','create',{title:'x',text:'y',[key]:true},{}));
  assert.throws(()=>P.validatePayload('ASANA','update',{due_on:'2026-02-30'},{}));
  assert.throws(()=>P.validatePayload('NOTION','create',{title:'CANON change',text:'x'},{}));
});
function asana(){let task={gid:'task',name:'Before',notes:'Body',due_on:null,completed:false,modified_at:'rev1',projects:[{gid:'project'}],parent:null,tags:[]};let calls=[];
  A._useBackend((provider,url,method,body)=>{assert.equal(provider,'ASANA');calls.push({url,method,body:clone(body||{})});
    if(method==='get') return {data:url.startsWith('projects/')?{gid:'project',name:'Synthetic',archived:false}:clone(task)};
    Object.assign(task,body.data);if(body.data.projects) task.projects=body.data.projects.map(gid=>({gid}));task.modified_at='rev2';return {data:{gid:'task'}};});
  return {calls,task};}
test('Asana creation and readback; planning is read-only',()=>{const b=asana();const a=proposal('tasks',null,'create',{title:'Created',text:'Details',due_on:'2026-09-16'});assert(b.calls.every(x=>x.method==='get'));const result=A.apply(a);assert.equal(result.status,'CONFIRMED');assert.equal(result.provider_object_id,'task');assert.equal(b.calls.filter(x=>x.method!=='get').length,1);});
test('Asana minimal updates preserve unrelated fields',()=>{const b=asana();const a=proposal('tasks','task','update',{completed:true});assert.equal(A.apply(a).status,'CONFIRMED');const body=b.calls.find(x=>x.method==='put').body;assert.deepEqual(body,{data:{completed:true}});assert.equal(b.task.name,'Before');});
test('edit conflict is rejected before mutation',()=>{const b=asana(),a=proposal('tasks','task','update',{title:'After'});b.task.modified_at='external-revision';assert.equal(A.apply(a).status,'REJECTED');assert(b.calls.every(x=>x.method==='get'));});
test('policy changes and stale snapshots reject before I/O',()=>{const b=asana(),a=proposal('tasks','task','update',{title:'After'});b.calls.length=0;P._useConfig({...policy,revision:'two'});assert.equal(A.apply(a).status,'REJECTED');assert.equal(b.calls.length,0);P._useConfig(policy);a.snapshot.inspected_at='2020-01-01T00:00:00Z';assert.equal(A.apply(a).status,'REJECTED');assert.equal(b.calls.length,0);});
test('unknown mutation result is uncertain and not retried',()=>{asana();const a=proposal('tasks','task','update',{title:'After'});let mutations=0;A._useBackend((p,u,m)=>{if(m!=='get'){mutations++;throw Error('cut');}return {data:{gid:'task',name:'Before',notes:'Body',modified_at:'rev1',projects:[{gid:'project'}],tags:[]}};});assert.equal(A.apply(a).status,'UNCERTAIN');assert.equal(mutations,1);});
test('multi-home and ritual tasks cannot be edited',()=>{const b=asana();b.task.projects.push({gid:'foreign'});assert.throws(()=>proposal('tasks','task','update',{title:'After'}));b.task.projects=[{gid:'project'}];b.task.tags=[{name:'Ritual'}];assert.throws(()=>proposal('tasks','task','update',{completed:true}));});
function notion(){const rt=text=>[{type:'text',text:{content:text},plain_text:text}];let title='Before',text='Body',revision='rev1',formatted=false;const calls=[];
  A._useBackend((p,u,m,b)=>{assert.equal(p,'NOTION');calls.push({u,m,b});const id=u.split('/')[1];
    if(m==='get' && u.includes('/children?')) return {results:[{id:'paragraph',type:'paragraph',has_children:false,paragraph:{rich_text:rt(text)}}]};
    if(m==='get' && u.startsWith('blocks/')) return id==='paragraph'?{id,type:'paragraph',parent:{type:'page_id',page_id:'page'},last_edited_time:revision,paragraph:{rich_text:formatted?[{...rt(text)[0],annotations:{bold:true}}]:rt(text)}}:{id,type:'child_page'};
    if(m==='get')return {id,object:'page',last_edited_time:revision,parent:id==='root'?{workspace:true}:{type:'page_id',page_id:'root'},properties:{title:{type:'title',title:rt(title)}}};
    revision='rev2';if(b.properties)title=b.properties.title.title.map(x=>x.text.content).join('');
    if(b.children)text=b.children.map(x=>x.paragraph.rich_text.map(t=>t.text.content).join('')).join('\n');
    if(b.paragraph)text=b.paragraph.rich_text.map(t=>t.text.content).join('');return {id:'page'};});
  return {calls,setFormatted:()=>{formatted=true;}};}
test('Notion create preserves multiline content, one mutation',()=>{const b=notion();const a=proposal('notes',null,'create',{title:'Created',text:'Line 1\nLine 2'});assert.equal(A.apply(a).status,'CONFIRMED');assert.equal(b.calls.filter(x=>x.m!=='get').length,1);});
test('Notion title and simple paragraph updates',()=>{notion();assert.equal(A.apply(proposal('notes','page','update',{title:'Renamed'})).status,'CONFIRMED');notion();assert.equal(A.apply(proposal('notes','paragraph','update',{text:'Replacement'})).status,'CONFIRMED');});
test('Notion rich paragraphs require targeted editing',()=>{const b=notion();b.setFormatted();assert.throws(()=>proposal('notes','paragraph','update',{text:'Replacement'}));assert(b.calls.every(x=>x.m==='get'));});
function drive(){let text='Before\n',rev='rev1',title='Document';const calls=[];
  A._useBackend((p,u,m,b)=>{calls.push({p,u,m,b});if(m==='get' && p==='DRIVE'){const id=u.split('/')[1].split('?')[0];return {id,name:title,mimeType:id==='folder'?'application/vnd.google-apps.folder':'application/vnd.google-apps.document',parents:id==='folder'?[]:['folder'],version:'1',capabilities:{canEdit:true}};}
    if(m==='get')return {revisionId:rev,tabs:[{documentTab:{body:{content:[{paragraph:{elements:[{textRun:{content:text}}]}}]}}}]};
    assert.equal(p,'DOCS');assert.equal(b.writeControl.requiredRevisionId,'rev1');const r=b.requests[0].replaceAllText;text=text.replace(r.containsText.text,r.replaceText);rev='rev2';return {replies:[{replaceAllText:{occurrencesChanged:1}}]};});return {calls};}
test('Google Docs revision-controlled exact replacement',()=>{const b=drive();assert.equal(A.apply(proposal('docs','doc','update',{old_text:'Before',text:'After'})).status,'CONFIRMED');assert.equal(b.calls.filter(x=>x.m!=='get').length,1);});
test('whole plan rejects cross context, expired handoff, duplicate target',()=>{asana();const a=proposal('tasks','task','update',{title:'After'}),p={step:{tool:'productive.propose',ordinal:1},source_context:'TEST',payload:a};const grant={allowed_tools:['productive.propose']};assert(P.validatePlan('id',[p],grant,'TEST',{}).plan.valid);assert(!P.validatePlan('id',[p,p],grant,'TEST',{}).plan.valid);assert(!P.validatePlan('id',[p],grant,'TEST',{blocked:true}).plan.valid);assert(!P.validatePlan('id',[p],grant,'OTHER',{}).plan.valid);assert(!P.validatePlan('id',[p],grant,'TEST',{handoff:{reconciled:true,expires_at:'2020-01-01'}}).plan.valid);});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../tests/Fixtures.gs'),'utf8'),c);
function engineRun(origin,audit=false,block=false){
  c.Fixtures.resetAll();Config._setRunLevel('LEVEL_3');const backend=asana();
  const producer=c.Fixtures.scriptedProvider(origin,[]);let turn=0;
  producer.completeWithTools=(req,tools)=>{
    turn++;
    if(turn===1)return producer.normalizeResponse({tool_requests:[{name:'productive.inspect',arguments:{destination:'tasks'},id:'inspect'}]});
    if(turn===2){const id=req.prompt.match(/"snapshot_id":"([^"]+)"/)[1];return producer.normalizeResponse({tool_requests:[{name:'productive.propose',arguments:{snapshot_id:id,operation:'create',payload:{title:'Synthetic engine note',text:'Verified context TEST'}},id:'propose'}]});}
    return producer.normalizeResponse({text:'Plan propuesto.'});
  };
  const other=origin==='OPENAI'?'ANTHROPIC':'OPENAI';
  const auditor=c.Fixtures.scriptedProvider(other,[{text:'BLOQUEO_MATERIAL: '+(block?'SI':'NO')+'\nEvaluación del plan.'}]);
  const result=c.Orchestrator.run('Contexto: TEST\nCrea una tarea de prueba.',{current_model:origin,providers:{[origin]:producer,[other]:auditor},requires_cross_audit:audit,mandate:{source:'LIVE_OPERATOR',requests_execution:true}});
  return {result,backend,auditor};
}
for(const origin of ['OPENAI','ANTHROPIC']) test('Real engine plans from '+origin+' without effects',()=>{
  const {result,backend}=engineRun(origin);assert.equal(result.status,'COMPLETED',JSON.stringify(result));assert.equal(result.write_plan.length,1);assert.equal(result.write_plan[0].action.context,'TEST');assert(backend.calls.every(x=>x.method==='get'));
});
test('Auditor receives concrete plan without proposing writes',()=>{const {result,auditor}=engineRun('OPENAI',true);assert.equal(result.status,'COMPLETED',JSON.stringify(result));assert.equal(result.write_plan.length,1);assert(auditor.calls[0].prompt.includes('Synthetic engine note'));assert(!auditor.calls[0].tools.includes('productive.propose'));});
test('Material audit block prevents actual plan release',()=>{const {result,backend}=engineRun('OPENAI',true,true);assert.equal(result.status,'REQUIRES_ANDRES');assert.equal(result.write_plan.length,0);assert(backend.calls.every(x=>x.method==='get'));});
test('Notion protects a paragraph inside a canonical ancestor',()=>{
  A._useBackend((p,u,m)=>{assert.equal(m,'get');const id=u.split('/')[1];
    if(u.startsWith('blocks/'))return id==='paragraph'?{id,type:'paragraph',last_edited_time:'r',parent:{type:'page_id',page_id:'canon'},paragraph:{rich_text:[]}}:{id,type:'child_page'};
    return {id,object:'page',last_edited_time:'r',parent:{type:'page_id',page_id:'root'},properties:{title:{type:'title',title:[{plain_text:'CANON Maestro'}]}}};
  });
  assert.throws(()=>proposal('notes','paragraph','update',{text:'replacement'}),/CANON_REQUIRES_OPERATOR/);
});
test('Drive protects documents inside canonical ancestors',()=>{
  A._useBackend((p,u,m)=>{assert.equal(m,'get');const id=u.split('/')[1].split('?')[0];return {id,name:id==='canon'?'CANON Derivado':'Routine',parents:[id==='doc'?'canon':'folder'],capabilities:{canEdit:true}};});
  assert.throws(()=>proposal('docs','doc','update',{old_text:'Before',text:'After'}),/CANON_REQUIRES_OPERATOR/);
});
test('Drive creation requires child access and verifies multipart conversion',()=>{
  let allowed=false,mutations=0,created=false;
  A._useBackend((p,u,m,b)=>{
    if(m==='get' && p==='DRIVE'){const id=u.split('/')[1].split('?')[0];return {id,name:id==='folder'?'Folder':'Created',mimeType:id==='folder'?'application/vnd.google-apps.folder':'application/vnd.google-apps.document',parents:id==='folder'?[]:['folder'],version:'1',capabilities:{canEdit:true,canAddChildren:allowed}};}
    if(m==='get' && p==='DOCS'){assert(created);return {revisionId:'r',body:{content:[{paragraph:{elements:[{textRun:{content:'Line 1\nLine 2\n'}}]}}]}};}
    assert.equal(p,'DRIVE_UPLOAD');assert.equal(m,'post');assert(b.includes('"parents":["folder"]'));assert(b.includes('Line 1\nLine 2'));mutations++;created=true;return {id:'doc'};
  });
  assert.throws(()=>proposal('docs',null,'create',{title:'Created',text:'Line 1\nLine 2'}),/DRIVE_CANNOT_CREATE_CHILD/);
  allowed=true;assert.equal(A.apply(proposal('docs',null,'create',{title:'Created',text:'Line 1\nLine 2'})).status,'CONFIRMED');assert.equal(mutations,1);
});
test('Distinct creations share a container without losing whole-plan validation',()=>{
  asana();const s=session(),snap=P.invoke(s,'productive.inspect',{destination:'tasks'});
  for(const title of ['First','Second'])P.invoke(s,'productive.propose',{snapshot_id:snap.snapshot_id,operation:'create',payload:{title,text:'Body'}});
  assert.equal(s.proposed_actions.length,2);
  const planned=s.proposed_actions.map((p,i)=>({step:{tool:'productive.propose',ordinal:i+1},source_context:'TEST',payload:p.payload}));
  assert(P.validatePlan('id',planned,s.grant,'TEST',{}).plan.valid);
});
test('Productive tool failure cannot report an executed write',()=>{
  c.Fixtures.resetAll();Config._setRunLevel('LEVEL_3');
  const producer=c.Fixtures.scriptedProvider('ANTHROPIC',[
    {tool_requests:[{name:'productive.inspect',arguments:{destination:'missing'},id:'bad'}]},
    {text:'Ya creé la nota.'}]);
  const result=c.Orchestrator.run('Contexto: TEST\nCrea una nota.',{current_model:'ANTHROPIC',providers:{ANTHROPIC:producer},mandate:{source:'LIVE_OPERATOR',requests_execution:true}});
  assert.equal(result.status,'REQUIRES_ANDRES');assert.equal(result.write_plan.length,0);assert(result.final_answer.startsWith('No se ejecutó ninguna escritura.'));
});
test('Tool failures are available to the next producer turn',()=>{
  c.Fixtures.resetAll();Config._setRunLevel('LEVEL_3');
  const producer=c.Fixtures.scriptedProvider('ANTHROPIC',[
    {tool_requests:[{name:'productive.inspect',arguments:{destination:'missing'},id:'bad'}]},
    {text:'Bloqueado por destino no permitido.'}]);
  c.Orchestrator.run('Contexto: TEST\nCrea una nota.',{current_model:'ANTHROPIC',providers:{ANTHROPIC:producer},mandate:{source:'LIVE_OPERATOR',requests_execution:true}});
  assert(producer.calls[1].prompt.includes('RESULTADOS DEL ÚLTIMO LOTE'));
  assert(producer.calls[1].prompt.includes('"ok":false'));
});
test('Repeated inspections cannot finish as a completed write',()=>{
  c.Fixtures.resetAll();Config._setRunLevel('LEVEL_3');const backend=asana();
  const producer=c.Fixtures.scriptedProvider('ANTHROPIC',Array.from({length:4},()=>({text:'Primero inspecciono.',tool_requests:[{name:'productive.inspect',arguments:{destination:'tasks'},id:'inspect'}]})));
  const result=c.Orchestrator.run('Contexto: TEST\nCrea una tarea.',{current_model:'ANTHROPIC',providers:{ANTHROPIC:producer},mandate:{source:'LIVE_OPERATOR',requests_execution:true}});
  assert.notEqual(result.status,'COMPLETED');assert.equal(result.write_plan.length,0);
  assert(backend.calls.every(x=>x.method==='get'));
});
console.log(`${tests} productive policy/adapter/engine tests passed; no network used.`);
