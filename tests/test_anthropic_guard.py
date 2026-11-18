"""Offline tests: _post is stubbed, no credentials or provider calls."""
import importlib.util, unittest
from pathlib import Path
from types import SimpleNamespace
from missingmcp.metis import direct_audit as m
class Tests(unittest.IsolatedAsyncioTestCase):
 async def audit(self,content,reason,origin='OPENAI'):
  cfg=SimpleNamespace(metis_anthropic_model='claude-sonnet-5',metis_anthropic_api_key='synthetic',metis_openai_model='gpt-6.1-sol',metis_openai_api_key='synthetic')
  audit=m.DirectAudit(cfg);calls=[]
  async def post(url,body,headers):
   calls.append(body)
   if origin=='OPENAI':
    return dict(id='synthetic-id',content=content,stop_reason=reason,model='claude-sonnet-5',usage={'input_tokens':100,'output_tokens':4096}),'synthetic-request'
   return dict(id='synthetic-id',output_text='complete',status='completed',model='gpt-6.1-sol',usage={'input_tokens':100,'output_tokens':10}),'synthetic-request'
  audit._post=post
  r=await audit.run(origin,'Synthetic audit','TEST')
  self.assertEqual(len(calls),1);self.assertEqual(r['writes_attempted'],0)
  if origin=='OPENAI':self.assertEqual(calls[0]['max_tokens'],4096)
  return r
 async def test_missing_or_thinking_max_retains_metadata(self):
  for blocks in [[],[{'type':'thinking','thinking':'PRIVATE_SYNTHETIC'}]]:
   r=await self.audit(blocks,'max_tokens')
   self.assertEqual(r['status'],'FAILED');self.assertEqual(r['error_code'],'OUTPUT_TOKEN_LIMIT')
   self.assertEqual(r['model'],'claude-sonnet-5');self.assertEqual(r['usage']['output_tokens'],4096)
   self.assertEqual(r['incomplete_reason'],'max_tokens');self.assertFalse(r['retryable'])
   self.assertNotIn('PRIVATE_SYNTHETIC',str(r));self.assertNotIn('final_answer',r)
 async def test_partial_text_not_success(self):
  r=await self.audit([{'type':'text','text':'partial'}],'max_tokens')
  self.assertEqual(r['error_code'],'OUTPUT_TOKEN_LIMIT');self.assertNotIn('final_answer',r)
 async def test_empty_end_turn_retains_metadata(self):
  r=await self.audit([],'end_turn')
  self.assertEqual(r['error_code'],'EMPTY_PROVIDER_RESPONSE');self.assertIsNotNone(r['usage']);self.assertEqual(r['provider_status'],'completed')
 async def test_nonterminal_reasons(self):
  for reason in ['pause_turn','tool_use',None,'new_future_reason','stop_sequence']:
   r=await self.audit([{'type':'text','text':'partial'}],reason)
   self.assertEqual(r['error_code'],'PROVIDER_NONTERMINAL')
 async def test_context_and_refusal(self):
  for reason,code in [('model_context_window_exceeded','PROVIDER_CONTEXT_LIMIT'),('refusal','PROVIDER_REFUSAL')]:
   r=await self.audit([{'type':'text','text':'partial'}],reason);self.assertEqual(r['error_code'],code)
 async def test_complete_end_turn(self):
  r=await self.audit([{'type':'text','text':'complete'}],'end_turn')
  self.assertEqual(r['status'],'COMPLETED');self.assertEqual(r['final_answer'],'complete')
 async def test_requires_andres(self):
  r=await self.audit([{'type':'text','text':'REQUIRES_ANDRES: decision'}],'end_turn')
  self.assertEqual(r['status'],'REQUIRES_ANDRES')
 async def test_openai_unchanged(self):
  r=await self.audit([],None,origin='ANTHROPIC');self.assertEqual(r['status'],'COMPLETED')
if __name__=='__main__':unittest.main(verbosity=2)
