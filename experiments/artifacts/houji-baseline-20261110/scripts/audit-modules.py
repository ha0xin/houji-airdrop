#!/usr/bin/env python3
"""Compare rebuilt modules with accepted upstream artifacts, without device access."""
import hashlib,json,struct
from pathlib import Path
from elftools.elf.elffile import ELFFile
R=(Path(__import__("os").environ["HOUJI_LEGACY_ROOT"])); B=R/'src/baseline'; reports=R/'artifacts/baseline';reports.mkdir(parents=True,exist_ok=True)
def inspect(p):
 with p.open('rb') as f:
  e=ELFFile(f); sy=e.get_section_by_name('.symtab')
  sec=e.get_section_by_name('__versions'); versions={}
  if sec:
   data=sec.data()
   for off in range(0,len(data),64):
    crc=struct.unpack_from('<Q',data,off)[0]; name=data[off+8:off+64].split(b'\0')[0].decode();versions[name]=f'0x{crc:08x}'
  info={}
  for row in e.get_section_by_name('.modinfo').data().split(b'\0'):
   if b'=' in row:
    k,v=row.split(b'=',1);info.setdefault(k.decode(),[]).append(v.decode())
  module=e.get_section_by_name('.gnu.linkonce.this_module');rel=e.get_section_by_name('.rela.gnu.linkonce.this_module')
  reloc=[]
  if rel:
   for r in rel.iter_relocations():reloc.append([r['r_offset'],r['r_info_type'],sy.get_symbol(r['r_info_sym']).name,r['r_addend']])
  exports={}
  for symbol in sy.iter_symbols():
   if symbol.name.startswith('__crc_') and isinstance(symbol['st_shndx'],int):
    section=e.get_section(symbol['st_shndx']);exports[symbol.name[6:]]=f"0x{struct.unpack_from('<I',section.data(),symbol['st_value'])[0]:08x}"
  executable={s.name:hashlib.sha256(s.data()).hexdigest() for s in e.iter_sections() if s['sh_flags']&4}
  alloc={s.name:dict(size=s['sh_size'],sha256=hashlib.sha256(s.data()).hexdigest()) for s in e.iter_sections() if s['sh_flags']&2 and s['sh_type']!='SHT_NOBITS'}
  return dict(exports=exports,executable=executable,sha256=hashlib.sha256(p.read_bytes()).hexdigest(),size=p.stat().st_size,machine=e['e_machine'],info=info,versions=versions,undefined=sorted(s.name for s in sy.iter_symbols() if s['st_shndx']=='SHN_UNDEF' and s.name),module_size=module['sh_size'],module_relocations=reloc,alloc=alloc)
result={}
for name,path in [('houji_wonder_bridge',B/'bridge'),('qca_cld3_kiwi_v2',B/'wlan/qcacld-3.0'),('wonder',B/'wonder')]:
 p=path/(name+'.ko')
 if not p.exists():continue
 a=inspect(R/'reference/runtime/modules'/p.name); b=inspect(p)
 common=a['versions'].keys()&b['versions'].keys()
 checks=dict(export_crcs=a['exports']==b['exports'],executable_bytes=a['executable']==b['executable'],machine=a['machine']==b['machine']=='EM_AARCH64',vermagic=a['info'].get('vermagic')==b['info'].get('vermagic'),depends=a['info'].get('depends')==b['info'].get('depends'),import_names=a['versions'].keys()==b['versions'].keys(),import_crcs=all(a['versions'][s]==b['versions'][s] for s in common),undefined=a['undefined']==b['undefined'],module_size=a['module_size']==b['module_size'],module_relocations=a['module_relocations']==b['module_relocations'])
 result[name]=dict(checks=checks,static_checks_pass=all(checks.values()),imports=len(b['versions']),added_imports=sorted(b['versions'].keys()-a['versions'].keys()),removed_imports=sorted(a['versions'].keys()-b['versions'].keys()),crc_mismatches={s:[a['versions'][s],b['versions'][s]] for s in common if a['versions'][s]!=b['versions'][s]},reference=a,rebuilt=b)
 print(name, json.dumps({k:v for k,v in result[name].items() if k not in ['reference','rebuilt']}))
# Check actual rebuilt provider CRCs against consumers, independent of the reference table.
providers={}
for name,v in result.items():
 for symbol,crc in v['rebuilt']['exports'].items():providers[symbol]=(name,crc)
for name,v in result.items():
 mismatches={s:{'consumer':c,'provider':providers[s]} for s,c in v['rebuilt']['versions'].items() if s in providers and providers[s][1]!=c}
 v['internal_provider_mismatches']=mismatches
 v['checks']['internal_providers']=not mismatches
 v['static_checks_pass']=all(v['checks'].values())
(reports/'module-comparison.json').write_text(json.dumps(result,indent=2)+'\n')
print('CAUTION: import CRCs use supplied symvers; static checks do not prove source type or runtime compatibility.')
