/** Provider actuator. Fixed endpoints, explicit fields, no generic HTTP tool.
 * Called only by ProductivePolicy.inspect (read) or the authenticated bridge
 * after its caller has durably recorded an action. Never retries a mutation.
 */
var ProductiveAdapter = (function () {
  var _backend = null;
  function fail(code) { ProductivePolicy.fail(code); }
  function request(provider, path, method, body, mime) {
    if (!ProductivePolicy.enabled()) { fail('PRODUCTIVE_WRITES_DISABLED'); }
    if (_backend) { return _backend(provider,path,method,body,mime); }
    var base, token;
    if (provider === 'NOTION') { base='https://api.notion.com/v1/'; token=Config.secret('NOTION_API_KEY'); }
    else if (provider === 'ASANA') { base='https://app.asana.com/api/1.0/'; token=Config.secret('ASANA_API_KEY'); }
    else if (provider === 'DOCS') { base='https://docs.googleapis.com/v1/'; token=ScriptApp.getOAuthToken(); }
    else if (provider === 'DRIVE') { base='https://www.googleapis.com/drive/v3/'; token=ScriptApp.getOAuthToken(); }
    else if (provider === 'DRIVE_UPLOAD') { base='https://www.googleapis.com/upload/drive/v3/'; token=ScriptApp.getOAuthToken(); }
    else { fail('PROVIDER_NOT_ALLOWED'); }
    var opts={method:method,muteHttpExceptions:true,followRedirects:false,headers:{Authorization:'Bearer '+token}};
    if (provider === 'NOTION') { opts.headers['Notion-Version']=Config.NOTION_VERSION; }
    if (body !== undefined) { opts.contentType=mime || 'application/json'; opts.payload=mime ? body : JSON.stringify(body); }
    var r=UrlFetchApp.fetch(base+path,opts), status=r.getResponseCode();
    if (status < 200 || status >= 300) { fail('PROVIDER_HTTP_'+status); }
    // Error bodies and auth headers never reach the model, ledger, or logs.
    return JSON.parse(r.getContentText());
  }
  function idPath(id) { ProductivePolicy.assertObject('OTHER',id); return encodeURIComponent(id); }
  function plain(rich) { return (rich || []).map(function(x){return x.plain_text || (x.text && x.text.content) || '';}).join(''); }
  function title(p) {
    var keys=Object.keys(p.properties || {});
    for (var i=0;i<keys.length;i++) { if (p.properties[keys[i]].type === 'title') { return plain(p.properties[keys[i]].title); } }
    return '';
  }
  function assertName(name) {
    if (/\bCANON\b|Canon Maestro|Canon Derivado/i.test(name || '')) { fail('CANON_REQUIRES_OPERATOR'); }
  }
  function notionObject(id) {
    var object=request('NOTION','blocks/'+idPath(id),'get');
    if (object.type === 'child_page') { return request('NOTION','pages/'+idPath(id),'get'); }
    return object;
  }
  function notionInspect(d,id) {
    ProductivePolicy.assertObject('NOTION',id);
    var object=notionObject(id), chain=[id], current=object;
    for (var depth=0; depth<30; depth++) {
      if (ProductivePolicy.normalizeId('NOTION',current.id) === ProductivePolicy.normalizeId('NOTION',d.root_id)) { break; }
      var parent=current.parent, pid=parent && (parent.page_id || parent.block_id || parent.database_id || parent.data_source_id);
      if (!pid) { break; }
      ProductivePolicy.assertObject('NOTION',pid);
      chain.push(pid);
      // Refuse crossing a database: shared database writes require an explicit
      // field-level context contract, not inheritance from a parent page.
      if (parent.type === 'database_id' || parent.type === 'data_source_id') { fail('SHARED_DATABASE_NOT_A_ROUTINE_PAGE'); }
      current=notionObject(pid);
      if (current.archived || current.in_trash) { fail('ARCHIVED_ANCESTOR'); }
    }
    ProductivePolicy.assertAncestry(d,chain);
    if (object.archived || object.in_trash) { fail('ARCHIVED_OBJECT'); }
    var page=object.object === 'page', name=page ? title(object) : '';
    assertName(name);
    var snap={object_id:id,kind:page?'page':'paragraph',title:name,text:'',revision:object.last_edited_time,ancestry:chain};
    if (!snap.revision) { fail('REVISION_UNAVAILABLE'); }
    if (!page) {
      if (object.type !== 'paragraph' || object.has_children || !(object.paragraph && Array.isArray(object.paragraph.rich_text))) { fail('UNSUPPORTED_NOTION_BLOCK'); }
      var rich=object.paragraph.rich_text;
      if (rich.some(function(r){return r.type !== 'text' || (r.text && r.text.link) ||
          Object.keys(r.annotations || {}).some(function(k){return k === 'color' ? r.annotations[k] !== 'default' : r.annotations[k] === true;});})) {
        fail('FORMATTED_NOTION_BLOCK_REQUIRES_TARGETED_EDITOR');
      }
      snap.text=plain(rich);
    } else {
      var res=request('NOTION','blocks/'+idPath(id)+'/children?page_size=100','get');
      if (res.has_more) { fail('NOTION_PAGE_TOO_LARGE_FOR_ROUTINE_EDITOR'); }
      snap.children=(res.results || []).filter(function(b){return b.type === 'paragraph' && !b.has_children;})
        .map(function(b){return {id:b.id,type:b.type,text:plain(b.paragraph.rich_text)};});
      snap.text=snap.children.map(function(b){return b.text;}).join('\n');
    }
    return snap;
  }
  function asanaInspect(d,id) {
    ProductivePolicy.assertObject('ASANA',id);
    if (id === d.root_id) {
      var project=request('ASANA','projects/'+idPath(id)+'?opt_fields=name,archived','get').data;
      if (!project || project.archived) { fail('PROJECT_UNAVAILABLE'); }
      return {object_id:id,kind:'container',title:project.name,text:'',revision:'container',ancestry:[id]};
    }
    function readTask(gid) { return request('ASANA','tasks/'+idPath(gid)+'?opt_fields=name,notes,due_on,completed,modified_at,projects.gid,parent.gid,permalink_url,tags.name','get').data; }
    var task=readTask(id), current=task, chain=[id], member=false;
    for(var depth=0;depth<30;depth++) {
      if (!current || !Array.isArray(current.projects)) { fail('TASK_MEMBERSHIP_UNSUPPORTED'); }
      if ((current.tags || []).some(function(t){return /ritual|canon|evaluaci[oó]n|caducidad/i.test(t.name || '');})) { fail('PROTECTED_RITUAL_TASK'); }
      assertName(current.name);
      if (current.projects.some(function(p){return p.gid!==d.root_id;})) { fail('TASK_MULTIHOMED'); }
      member=member || current.projects.some(function(p){return p.gid===d.root_id;});
      if (!current.parent) { break; }
      var pid=current.parent.gid;ProductivePolicy.assertObject('ASANA',pid);
      if (chain.indexOf(pid)>=0 || depth===29) { fail('TASK_ANCESTRY_UNRESOLVED'); }
      chain.push(pid);current=readTask(pid);
    }
    if (!member) { fail('TASK_OUTSIDE_PROJECT'); }
    if (!task.modified_at) { fail('REVISION_UNAVAILABLE'); }
    chain.push(d.root_id);ProductivePolicy.assertAncestry(d,chain);
    return {object_id:id,kind:'task',title:task.name,text:task.notes || '',revision:task.modified_at,ancestry:chain,
      current:{due_on:task.due_on || null,completed:task.completed},url:task.permalink_url};
  }
  function driveMeta(id) { return request('DRIVE','files/'+idPath(id)+'?supportsAllDrives=true&fields=id,name,mimeType,parents,trashed,version,capabilities(canEdit,canAddChildren),webViewLink','get'); }
  function documentText(value) {
    if (!value || typeof value !== 'object') { return ''; }
    if (value.textRun) { return value.textRun.content || ''; }
    if (Array.isArray(value)) { return value.map(documentText).join(''); }
    return Object.keys(value).filter(function(k){return k !== 'body' || !value.tabs;})
      .map(function(k){return documentText(value[k]);}).join('');
  }
  function driveInspect(d,id) {
    ProductivePolicy.assertObject('DRIVE',id);
    var file=driveMeta(id), chain=[id], current=file;
    for(var depth=0;depth<30 && current.id!==d.root_id;depth++) {
      if (!current.parents || current.parents.length!==1) { fail('DRIVE_PARENT_UNRESOLVED'); }
      var pid=current.parents[0]; ProductivePolicy.assertObject('DRIVE',pid);
      chain.push(pid); current=driveMeta(pid);
      if (current.trashed) { fail('ARCHIVED_ANCESTOR'); }
    }
    ProductivePolicy.assertAncestry(d,chain);
    if (file.trashed || !file.capabilities || file.capabilities.canEdit !== true) { fail('DRIVE_NOT_EDITABLE'); }
    assertName(file.name);
    if (id===d.root_id) {
      if (file.mimeType!=='application/vnd.google-apps.folder') { fail('DESTINATION_NOT_FOLDER'); }
      return {object_id:id,kind:'container',title:file.name,text:'',revision:String(file.version),ancestry:chain};
    }
    if (file.mimeType!=='application/vnd.google-apps.document') { fail('ROUTINE_EDITOR_SUPPORTS_GOOGLE_DOCUMENTS'); }
    var doc=request('DOCS','documents/'+idPath(id)+'?includeTabsContent=true','get');
    if (!doc.revisionId) { fail('REVISION_UNAVAILABLE'); }
    return {object_id:id,kind:'document',title:file.name,text:documentText(doc),revision:doc.revisionId,ancestry:chain,
      url:file.webViewLink || 'https://docs.google.com/document/d/'+id+'/edit'};
  }
  function inspect(d,id) {
    var value;
    if (d.provider==='NOTION') { value=notionInspect(d,id); }
    else if(d.provider==='ASANA') { value=asanaInspect(d,id); }
    else if(d.provider==='DRIVE') { value=driveInspect(d,id); }
    else { fail('PROVIDER_NOT_ALLOWED'); }
    if (value.text.length > 16000) { fail('OBJECT_TOO_LARGE_FOR_ROUTINE_EDITOR'); }
    return value;
  }
  function rich(text) {
    var parts=[];
    for(var i=0;i<Math.max(text.length,1);i+=1900) { parts.push({type:'text',text:{content:text.slice(i,i+1900)}}); }
    return parts;
  }
  function paragraphs(text) {
    var blocks=[];
    text.split('\n').forEach(function(line){
      blocks.push({object:'block',type:'paragraph',paragraph:{rich_text:rich(line)}});
    });
    if (blocks.length>100) { fail('TOO_MANY_PARAGRAPHS'); }
    return blocks;
  }
  function mutation(d,a) {
    var p=a.payload,id=a.object_id;
    if(d.provider==='NOTION') {
      if(a.operation==='create') {
        return request('NOTION','pages','post',{parent:{type:'page_id',page_id:d.root_id},
          properties:{title:{type:'title',title:rich(p.title)}},children:paragraphs(p.text)}).id;
      }
      if(a.snapshot.kind==='page') { request('NOTION','pages/'+idPath(id),'patch',{properties:{title:{type:'title',title:rich(p.title)}}}); }
      else { request('NOTION','blocks/'+idPath(id),'patch',{paragraph:{rich_text:rich(p.text)}}); }
      return id;
    }
    if(d.provider==='ASANA') {
      var data={};
      if ('title' in p) { data.name=p.title; }
      if ('text' in p) { data.notes=p.text; }
      if ('due_on' in p) { data.due_on=p.due_on; }
      if ('completed' in p) { data.completed=p.completed; }
      if(a.operation==='create') { data.projects=[d.root_id]; data.assignee='me'; return request('ASANA','tasks','post',{data:data}).data.gid; }
      request('ASANA','tasks/'+idPath(id),'put',{data:data}); return id;
    }
    if(a.operation==='create') {
      var boundary='metis_'+Schemas.uuid().replace(/-/g,'');
      var body='--'+boundary+'\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n'+JSON.stringify({name:p.title,mimeType:'application/vnd.google-apps.document',parents:[d.root_id]})+
        '\r\n--'+boundary+'\r\nContent-Type: text/plain; charset=UTF-8\r\n\r\n'+p.text+'\r\n--'+boundary+'--';
      return request('DRIVE_UPLOAD','files?uploadType=multipart&supportsAllDrives=true&fields=id','post',body,'multipart/related; boundary='+boundary).id;
    }
    var result=request('DOCS','documents/'+idPath(id)+':batchUpdate','post',{
      writeControl:{requiredRevisionId:a.snapshot.revision},
      requests:[{replaceAllText:{containsText:{text:p.old_text,matchCase:true},replaceText:p.text}}]});
    if (!result.replies || result.replies.length!==1 || result.replies[0].replaceAllText.occurrencesChanged!==1) { fail('REPLACEMENT_COUNT_UNEXPECTED'); }
    return id;
  }
  function verify(a,snapshot) {
    var p=a.payload;
    if ('title' in p && snapshot.title!==p.title) { return false; }
    if (a.provider==='DRIVE') {
      if (a.operation==='create') { return snapshot.text.replace(/\n$/,'')===p.text.replace(/\n$/,''); }
      return snapshot.text===a.snapshot.text.replace(p.old_text,p.text);
    }
    if ('text' in p && snapshot.text!==p.text) { return false; }
    if ('due_on' in p && snapshot.current.due_on!==p.due_on) { return false; }
    if ('completed' in p && snapshot.current.completed!==p.completed) { return false; }
    return true;
  }
  function apply(a) {
    var dispatched=false,id=null;
    try {
      var d=ProductivePolicy.check(a), fresh=inspect(d,a.object_id);
      if (fresh.kind!==a.snapshot.kind || (a.operation==='update' && fresh.revision!==a.snapshot.revision)) { fail('EDIT_CONFLICT'); }
      // Preflight again immediately before the only mutation. No generic URL,
      // HTTP verb, parent, properties, sharing, or archive field is accepted.
      ProductivePolicy.validatePayload(d.provider,a.operation,a.payload,fresh);
      if (d.provider==='NOTION' && a.operation==='create') { paragraphs(a.payload.text); }
      ProductivePolicy.check(a);
      dispatched=true;
      id=mutation(d,a);
      if (!id || typeof id!=='string') { fail('PROVIDER_ID_MISSING'); }
      var after=inspect(d,id);
      if (!verify(a,after)) { fail('READBACK_MISMATCH'); }
      return {status:'CONFIRMED',provider_object_id:id,verified:true,provider:d.provider,
        url:after.url || (d.provider==='NOTION' ? 'https://www.notion.so/'+id.replace(/-/g,'') : 'https://app.asana.com/0/0/'+id)};
    } catch(e) {
      return {status:dispatched?'UNCERTAIN':'REJECTED',provider_object_id:id,verified:false,
        error:Errors.redactText(e.message),requires_review:dispatched};
    }
  }
  return {inspect:inspect,apply:apply,verify:verify,documentText:documentText,_useBackend:function(fn){_backend=fn;}};
})();
