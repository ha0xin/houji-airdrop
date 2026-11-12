#!/usr/bin/env python3
import hashlib,json,shutil,subprocess
from pathlib import Path
from elftools.elf.elffile import ELFFile
R=(Path(__import__("os").environ["HOUJI_LEGACY_ROOT"])); report=R/'artifacts/baseline/module-comparison.json'
data=json.loads(report.read_text())
assert len(data)==3 and all(v['static_checks_pass'] for v in data.values())
bundle=R/'artifacts/houji-baseline-20261110';bundle.mkdir(exist_ok=True)
for n in ['modules','reference','reports','scripts','configs']:(bundle/n).mkdir(exist_ok=True)
def allocated(p):
 with p.open('rb') as f:
  return {s.name:(s['sh_size'],hashlib.sha256(s.data()).hexdigest()) for s in ELFFile(f).iter_sections() if s['sh_flags']&2}
for name,sub in [('houji_wonder_bridge','bridge'),('qca_cld3_kiwi_v2','wlan/qcacld-3.0'),('wonder','wonder')]:
 src=R/'src/baseline'/sub/(name+'.ko');dst=bundle/'modules'/src.name
 shutil.copyfile(src,dst)
 subprocess.run([str(R/'toolchains/clang-r487747c/bin/llvm-strip'),'--strip-debug',str(dst)],check=True)
 assert allocated(src)==allocated(dst),name
 shutil.copyfile(R/'reference/runtime/modules'/src.name,bundle/'reference'/src.name)
shutil.copyfile(report,bundle/'reports/module-comparison.json')
for p in (R/'logs').glob('*'):
 if p.is_file() and ('baseline' in p.name or p.name in ['module-audit.log','source-prepare.log','packages.tsv','environment-smoke.log']):shutil.copyfile(p,bundle/'reports'/p.name)
for p in (R/'host').glob('*'):
 if p.is_file():shutil.copyfile(p,bundle/'scripts'/p.name)
for name in ['bridge','wlan','wonder']:
 shutil.copyfile(R/'out'/('baseline-'+name)/'.config',bundle/'configs'/(name+'.config'))
 shutil.copyfile(R/'out'/('baseline-'+name)/'include/generated/utsrelease.h',bundle/'configs'/(name+'-utsrelease.h'))
for p in (R/'downloads').glob('SHA256SUMS.*'):shutil.copyfile(p,bundle/'reports'/p.name)
(bundle/'README.md').write_text('''# houji 离线重建基线 · 2026-11-10

状态：三个模块编译与参考件静态对照通过；未连接手机，未做设备验收，未加入提速改动。

`modules/` 是本次编译的去调试信息产物；`reference/` 是已使用的上游 open-runtime-v0.5.1-preview.1/runtime/modules 对照和回退件。去调试前后全部 SHF_ALLOC 节大小及内容哈希一致。完整 DWARF 产物保留在 build-host /work/houji-build/src/baseline。

导入符号计数：bridge 30、WLAN 567、Wonder 103。导入/导出 CRC、未解析符号列表、vermagic、依赖、module 节大小/重定位以及可执行节字节均与参考件一致；模块之间 provider CRC 亦一致。整文件哈希不相同，存在路径字符串、build-id 和调试信息等构建差异。CRC 由配方 symvers 提供，因此 CRC 一致不单独证明类型和运行时兼容。

模块 vermagic 前缀为参考件实际的 6.1.68，手机内核为 6.1.138-android14-11-g0c3d559bcd85-ab14529422。没有修改手机、固件或系统分区。

构建记录保留了两处已有 WLAN 栈帧超限告警（2496 / 3344 字节），以及无匹配 vmlinux 导致的 BTF 跳过信息。未伪造 vmlinux/BTF，也未为消除告警修改基线代码。源码为归档而无 .git，因此版本查询会打印非仓库提示，但 make 和最终对照成功。

下一步按 scripts/DEVICE-TEST.md 完成基线装载、发现、双向短文件校验、Wi-Fi 恢复和重复启停；通过后再测试性能改动。这个包没有自动安装入口。
''')
files=sorted(p for p in bundle.rglob('*') if p.is_file() and p.name!='SHA256SUMS')
(bundle/'SHA256SUMS').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+str(p.relative_to(bundle))+'\n' for p in files))
subprocess.run(['sha256sum','-c','SHA256SUMS'],cwd=bundle,check=True,stdout=subprocess.DEVNULL)
subprocess.run(['tar','-czf',str(bundle)+'.tar.gz','-C',str(bundle.parent),bundle.name],check=True)
print('PACKAGE_VERIFIED',bundle)
print(hashlib.sha256(Path(str(bundle)+'.tar.gz').read_bytes()).hexdigest())
