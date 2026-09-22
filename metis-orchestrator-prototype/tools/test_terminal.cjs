const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const c=vm.createContext({Number,JSON,Errors:{configError:s=>Error(s),schemaError:s=>Error(s)}});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/TerminalOutput.gs'),'utf8'),c);
vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/ProviderAdapter.gs'),'utf8'),c);
vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/OpenAIAdapter.gs'),'utf8'),c);
const request={system:'POLICY',prompt:'OPERATOR_REQUEST\nPRODUCER\nAUDIT'};
const partial='DICTAMEN: RECHAZAR\n'+'Análisis con evidencia. '.repeat(20);
const response=(text,stop_reason,tool_requests=[])=>({text,stop_reason,tool_requests,provider_request_id:'synthetic'});
let passed=0;
function test(name,fn){fn();passed++;console.log('PASS '+name);}
test('complete result needs one invocation',()=>{let calls=0;assert.equal(c.TerminalOutput.complete(request,()=>{calls++;return response('Final','completed');},{MAX_TERMINAL_CONTINUATIONS:2},[]).text,'Final');assert.equal(calls,1);});
test('anchored continuation preserves full request and removes exact seam duplication',()=>{
  let calls=0;const trace=[];
  const out=c.TerminalOutput.complete(request,r=>{calls++;if(calls===1)return response(partial,'max_tokens');
    assert.ok(r.prompt.startsWith(request.prompt));assert.ok(r.system.startsWith(request.system));
    const state=JSON.parse(r.prompt.slice(r.prompt.lastIndexOf('\n')+1));assert.equal(state.partial_text,partial);
    return response(state.exact_resume_anchor+'Conclusión completa.','end_turn');
  },{MAX_TERMINAL_CONTINUATIONS:2},trace);
  assert.equal(out.text,partial+'Conclusión completa.');assert.equal(trace.length,2);
});
test('exhausted continuation never returns partial output',()=>{
  let calls=0;assert.throws(()=>c.TerminalOutput.complete(request,r=>{calls++;if(calls===1)return response(partial,'length');const state=JSON.parse(r.prompt.slice(r.prompt.lastIndexOf('\n')+1));return response(state.exact_resume_anchor+'more','length');},{MAX_TERMINAL_CONTINUATIONS:2},[]),/incomplete/);assert.equal(calls,3);
});
for(const reason of ['incomplete','refusal',null])test('non-length stop fails without recovery: '+reason,()=>{let calls=0;assert.throws(()=>c.TerminalOutput.complete(request,()=>{calls++;return response(partial,reason);},{MAX_TERMINAL_CONTINUATIONS:2},[]));assert.equal(calls,1);});
test('tools rejected at terminal',()=>assert.throws(()=>c.TerminalOutput.complete(request,()=>response('x','end_turn',[{}]),{MAX_TERMINAL_CONTINUATIONS:2},[]),/no tool/));
test('anchor mismatch rejected',()=>{let calls=0;assert.throws(()=>c.TerminalOutput.complete(request,()=>++calls===1?response(partial,'length'):response('unrelated','end_turn'),{MAX_TERMINAL_CONTINUATIONS:2},[]),/anchor/);});
test('no progress rejected',()=>{let calls=0;assert.throws(()=>c.TerminalOutput.complete(request,()=>++calls===1?response(partial,'length'):response(partial.slice(-160),'end_turn'),{MAX_TERMINAL_CONTINUATIONS:2},[]),/progress/);});
test('budget or intervention gate stops recovery without swallowing error',()=>{let calls=0;assert.throws(()=>c.TerminalOutput.complete(request,()=>{if(++calls===1)return response(partial,'length');throw Error('BUDGET_EXCEEDED');},{MAX_TERMINAL_CONTINUATIONS:2},[]),/BUDGET_EXCEEDED/);});
test('disabled recovery does not invoke again',()=>{let calls=0;assert.throws(()=>c.TerminalOutput.complete(request,()=>{calls++;return response(partial,'length');},{MAX_TERMINAL_CONTINUATIONS:0},[]));assert.equal(calls,1);});
test('invalid policy rejected before invocation',()=>assert.throws(()=>c.TerminalOutput.complete(request,()=>assert.fail('called'),{MAX_TERMINAL_CONTINUATIONS:Infinity},[])));
for(const reason of ['max_output_tokens','content_filter',undefined])test('OpenAI incomplete reason '+reason,()=>{
  const result=c.OpenAIAdapter.create().normalizeResponse({status:'incomplete',incomplete_details:reason?{reason}:undefined,output:[]});
  assert.equal(result.stop_reason,reason==='max_output_tokens'?'max_tokens':'incomplete');
});
console.log(passed+' terminal completion tests passed; no network');
