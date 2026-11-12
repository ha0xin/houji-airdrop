#!/usr/bin/env python3
"""Summarize counter deltas; refuse reset/discontinuous or malformed captures."""
import json,sys
from pathlib import Path
def parse(raw):
 lines=raw.strip().splitlines();assert len(lines)==5,'truncated stats'
 def fields(line):
  return {k:([int(x) for x in v.split(',')] if ',' in v else int(v)) for k,v in (s.split('=',1) for s in line.split())}
 out=fields(lines[0]);out.update(fields(lines[1]));out['classes']=[fields(x) for x in lines[2:]]
 assert out['schema']==1 and out['enabled']==1
 assert [c['class'] for c in out['classes']]==[0,1,2]
 for c in out['classes']:assert len(c['delay_bins'])==8 and len(c['status_bins'])==17
 assert out['admitted']==out['active']+out['completed']+out['release_without_status'],'ledger invariant'
 return out
def summarize(rows):
 assert len(rows)>=2
 samples=[parse(r['stats']) for r in rows]
 # Check every adjacent pair: never silently combine data across a module reload.
 for a,b in zip(samples,samples[1:]):
  assert a['epoch_ns']==b['epoch_ns'],'module was reloaded'
  assert b['monotonic_ns']>a['monotonic_ns'],'clock did not advance'
  for k in ['calls','accepted','rejected','admitted','completed','release_without_status','overflow','duplicate','unmatched']:
   assert b[k]>=a[k], 'counter reset: '+k
  for ca,cb in zip(a['classes'],b['classes']):
   for k in ['completed','bytes','delay_ns','tries','max_ns']:assert cb[k]>=ca[k], 'class counter reset'
   for k in ['delay_bins','status_bins']:assert all(y>=x for x,y in zip(ca[k],cb[k]))
 a,b=samples[0],samples[-1];seconds=(b['monotonic_ns']-a['monotonic_ns'])/1e9
 out={'seconds':seconds,'start_active':a['active'],'end_active':b['active'],
      'end_oldest_ms':b['oldest_ns']/1e6,'sampled_peak_active':max(s['active'] for s in samples),
      'module_lifetime_highwater':b['highwater'],
      'delta':{k:b[k]-a[k] for k in ['calls','accepted','rejected','admitted','completed','release_without_status','overflow','duplicate','unmatched']},'classes':[]}
 for name,ca,cb in zip(['unicast_data','multicast_data','management_control'],a['classes'],b['classes']):
  n=cb['completed']-ca['completed'];lat=cb['delay_ns']-ca['delay_ns'];tries=cb['tries']-ca['tries']
  out['classes'].append(dict(name=name,completed=n,start_active=ca['active'],end_active=cb['active'],raw_completed_MB_s=(cb['bytes']-ca['bytes'])/seconds/1e6,mean_submit_to_status_ms=lat/n/1e6 if n else None,mean_transmit_count=tries/n if n else None,delay_bins=[y-x for x,y in zip(ca['delay_bins'],cb['delay_bins'])],status_bins=[y-x for x,y in zip(ca['status_bins'],cb['status_bins'])]))
 out['interpretation']='Raw frame completion rate is not file throughput. Delay includes host enqueue, firmware/air retries and completion reporting. status_bins are raw HAL values. Overflow/duplicate/unmatched or reset invalidates exact in-flight interpretation.'
 return out
if __name__=='__main__':
 print(json.dumps(summarize([json.loads(s) for s in Path(sys.argv[1]).read_text().splitlines()]),indent=2))
