#!/usr/bin/env python3
"""Offline baseline rebuild; never deploys. Preserves the published input snapshot."""
import json, os, shutil, subprocess, sys
from pathlib import Path
R=(Path(__import__("os").environ["HOUJI_LEGACY_ROOT"])); U=R/'upstream'; S=R/'src'; B=S/'baseline'
# Match the accepted artifacts, whose vermagic prefix is 6.1.68.
# Phone release is tracked separately; no binary vermagic patching.
release='6.1.68'
env=os.environ.copy(); env['PATH']=str(R/'toolchains/clang-r487747c/bin')+':'+env['PATH']; env['TMPDIR']=str(R/'tmp')
def run(args):
    print('+',repr([str(x) for x in args]),flush=True)
    subprocess.run([str(x) for x in args],env=env,check=True)
B.mkdir(exist_ok=True)
for name in ['bridge','wonder']:
    if not (B/name).exists():shutil.copytree(U/'kernel'/name,B/name)
if not (B/'wlan').exists():
    shutil.copytree(S/'wlan',B/'wlan',symlinks=True)
    run(['patch','--batch','--fuzz=0','-R','-p1','-d',B/'wlan/qcacld-3.0/core/hdd','-i',U/'kernel/legacy-to-framework.patch'])
for name in sys.argv[1:] or ['bridge','wlan','wonder']:
    out=R/'out'/('baseline-'+name);out.mkdir(exist_ok=True)
    env['WONDER_SOURCE']=str(B/'wonder')
    common=['make','-C',S/'kernel',f'O={out}','ARCH=arm64','LLVM=1','LLVM_IAS=1',f'KERNELRELEASE={release}','PAHOLE=/usr/bin/pahole']
    if name=='wonder':common += [f'KBUILD_KCONFIG={U}/kernel/configs/Kconfig.wonder']
    shutil.copyfile(U/'kernel/configs'/f'{name}.config',out/'.config')
    run(common+['olddefconfig']);run(common+['-j4','modules_prepare'])
    shutil.copyfile(U/'kernel/configs/kernel.symvers',out/'Module.symvers')
    extra=R/'out/wlan-build-extra.symvers'
    if name=='wlan':
        # Replace reference bridge exports with those from this build.
        bridge=(B/'bridge/Module.symvers').read_text().splitlines()
        symbols={x.split()[1] for x in bridge}
        ref=(U/'kernel/configs/wlan-extra.symvers').read_text().splitlines()
        extra.write_text('\n'.join([x for x in ref if x.split()[1] not in symbols]+bridge)+'\n')
    vals=dict(KERNEL=S/'kernel',KERNEL_OUT=out,WLAN=B/'wlan',BRIDGE=B/'bridge',WONDER=B/'wonder',EXTRA_SYMVERS=extra,IPA_INCLUDE=U/'kernel/dataipa-include',EXTERNAL_INCLUDE=U/'kernel/external-include',PAHOLE='/usr/bin/pahole',WONDER_PROVIDERS=U/'kernel/configs/wonder-providers.symvers',KCONFIG_WONDER=U/'kernel/configs/Kconfig.wonder')
    args=json.loads((U/'kernel/build-commands.json').read_text())[name]
    for key,val in vals.items():args=[x.replace('${'+key+'}',str(val)) for x in args]
    args.append(f'KERNELRELEASE={release}')
    run(args)
    print('BASELINE_BUILD_PASS='+name,flush=True)
