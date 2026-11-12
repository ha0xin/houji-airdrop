import importlib.util
from pathlib import Path
s=importlib.util.spec_from_file_location('summary',Path(__file__).with_name('summarize-observer.py'));m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
def row(epoch,ns,n):
 raw=f'schema=1 enabled=1 epoch_ns={epoch} monotonic_ns={ns} calls={n} accepted={n} rejected=0\n'
 raw+=f'admitted={n} active=0 highwater=1 completed={n} release_without_status=0 overflow=0 duplicate=0 unmatched=0 oldest_ns=0\n'
 for c in range(3):
  k=n if c==0 else 0
  raw+=f'class={c} completed={k} bytes={k*1000} delay_ns={k*1000000} max_ns=1000000 tries={k*2} active=0 highwater=1 delay_bins=0,{k},0,0,0,0,0,0 status_bins={k},'+','.join(['0']*16)+'\n'
 return {'stats':raw}
a=row(1,1000000000,10);b=row(1,2000000000,20)
x=m.summarize([a,b]);assert x['classes'][0]['mean_submit_to_status_ms']==1
assert x['classes'][0]['mean_transmit_count']==2
assert x['classes'][0]['raw_completed_MB_s']==0.01
for bad in [row(2,3000000000,30),row(1,3000000000,1),{'stats':b['stats'][:-30]}]:
 try:m.summarize([a,b,bad])
 except (AssertionError,ValueError):pass
 else:raise AssertionError('invalid capture accepted')
print('SUMMARY_TEST_PASS: deltas, reset with larger counters, decrement, truncation')
