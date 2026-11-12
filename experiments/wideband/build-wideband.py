#!/usr/bin/env python3
import os,json,shutil,subprocess
from pathlib import Path
R=(Path(__import__("os").environ["HOUJI_LEGACY_ROOT"])); U=R/'upstream'; B=R/'src/baseline'; W=R/'src/wideband/wlan'; O=R/'out/wideband-wlan'
env=os.environ.copy();env['PATH']=str(R/'toolchains/clang-r487747c/bin')+':'+env['PATH'];env['TMPDIR']=str(R/'tmp')
def run(a):
 print('+',repr([str(x) for x in a]),flush=True);subprocess.run([str(x) for x in a],env=env,check=True)
O.mkdir(exist_ok=True);shutil.copyfile(U/'kernel/configs/wlan.config',O/'.config')
common=['make','-C',R/'src/kernel',f'O={O}','ARCH=arm64','LLVM=1','LLVM_IAS=1','KERNELRELEASE=6.1.68','PAHOLE=/usr/bin/pahole']
run(common+['olddefconfig']);run(common+['-j4','modules_prepare'])
shutil.copyfile(U/'kernel/configs/kernel.symvers',O/'Module.symvers')
v=dict(KERNEL=R/'src/kernel',KERNEL_OUT=O,WLAN=W,EXTRA_SYMVERS=R/'out/wlan-build-extra.symvers',IPA_INCLUDE=U/'kernel/dataipa-include',EXTERNAL_INCLUDE=U/'kernel/external-include',PAHOLE='/usr/bin/pahole')
a=json.loads((U/'kernel/build-commands.json').read_text())['wlan']
for k,val in v.items():a=[x.replace('${'+k+'}',str(val)) for x in a]
run(a+['KERNELRELEASE=6.1.68'])
print('WIDEBAND_BUILD_PASS')
