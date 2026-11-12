#!/usr/bin/env python3
import hashlib,json,shutil,subprocess
from pathlib import Path
from elftools.elf.elffile import ELFFile
R=(Path(__import__("os").environ["HOUJI_LEGACY_ROOT"])); base=R/'artifacts/houji-baseline-20261110'; dest=R/'artifacts/houji-tx-observer-20261110'
audit=R/'artifacts/observer/module-comparison.json'
assert json.loads(audit.read_text())['offline_checks_pass']
for d in ['modules','rollback-baseline','source','reports']:(dest/d).mkdir(parents=True,exist_ok=True)
for p in (base/'modules').glob('*.ko'):
 shutil.copyfile(p,dest/'modules'/p.name);shutil.copyfile(p,dest/'rollback-baseline'/p.name)
src=R/'src/observer/wlan/qcacld-3.0/qca_cld3_kiwi_v2.ko';target=dest/'modules'/src.name
shutil.copyfile(src,target)
def alloc(p):
 with p.open('rb') as f:return {s.name:(s['sh_size'],hashlib.sha256(s.data()).hexdigest()) for s in ELFFile(f).iter_sections() if s['sh_flags']&2}
subprocess.run([str(R/'toolchains/clang-r487747c/bin/llvm-strip'),'--strip-debug',str(target)],check=True)
assert alloc(src)==alloc(target)
for p in (R/'observer').iterdir():
 if p.is_file():shutil.copyfile(p,dest/'source'/p.name)
for name in ['observer-build.log','observer-build-final.log','observer-audit.log','observer-tests.log']:
 p=R/'logs'/name
 if p.exists():shutil.copyfile(p,dest/'reports'/name)
shutil.copyfile(audit,dest/'reports/module-comparison.json')
shutil.copyfile(R/'host/DEVICE-TEST.md',dest/'BASELINE-DEVICE-TEST.md')
(dest/'README.md').write_text('''# houji TX 观测测试包 · 2026-11-10

仅离线编译/静态检查通过，未连接手机，未证明性能改善。

modules/ 是重建桥接、Wonder及被动观测WLAN；rollback-baseline/ 是未修改逻辑的重建基线。手机原有精确模块备份与原厂恢复流程仍需保留。源文件、补丁、只读采样与分析工具在 source/，详细语义见 source/README.md。

必须先验收基线，然后单独加载本版，并比较 pt_tx_observe=0 与 pt_tx_observe=1；默认关闭，不能在线切换。不修改常用运行脚本、不自动安装、不永久替换系统驱动。启用后的开销与实际回收行为尚待测量。

既有公共符号CRC、依赖、vermagic、模块大小/关键重定位匹配基线；可执行代码按预期变化。完整报告见 reports/module-comparison.json。读取统计并不提供新的ACK反馈，也不改变发包节奏、信道或固定速率。
''')
files=sorted(p for p in dest.rglob('*') if p.is_file() and p.name!='SHA256SUMS')
(dest/'SHA256SUMS').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+str(p.relative_to(dest))+'\n' for p in files))
subprocess.run(['sha256sum','-c','SHA256SUMS'],cwd=dest,check=True,stdout=subprocess.DEVNULL)
subprocess.run(['tar','-czf',str(dest)+'.tar.gz','-C',str(dest.parent),dest.name],check=True)
print('OBSERVER_PACKAGE_VERIFIED',hashlib.sha256(Path(str(dest)+'.tar.gz').read_bytes()).hexdigest())
