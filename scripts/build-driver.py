#!/usr/bin/env python3
"""Build all three modules from BYOB source archives and this repository only."""
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile

REPO = Path(__file__).resolve().parents[1]
UPSTREAM = REPO / 'third_party/upstream'
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--check', action='store_true', help='check inputs without building')
args = parser.parse_args()

def required(name):
    value = os.environ.get(name)
    if not value:
        sys.exit(f'BYOB: set {name}; see docs/guides/byob.md')
    path = Path(value).expanduser().resolve()
    if not path.exists():
        sys.exit(f'BYOB: {name} does not exist: {path}')
    return path

kernel_archive = required('HOUJI_KERNEL_ARCHIVE')
wlan_archive = required('HOUJI_WLAN_ARCHIVE')
clang = required('HOUJI_CLANG_DIR')
if platform.system() != 'Linux' or platform.machine() not in ('x86_64', 'amd64'):
    sys.exit('Build driver on Linux x86_64 with the Android Clang toolchain; see docs/guides/building.md')
for tool in ['make', 'patch', 'flex', 'bison', 'pahole', 'perl']:
    if not shutil.which(tool):
        sys.exit(f'Build dependency missing: {tool}')
if not (clang / 'bin/clang').is_file():
    sys.exit('HOUJI_CLANG_DIR must contain bin/clang')
if args.check:
    print('Driver BYOB inputs available (source compatibility checked during preparation).')
    sys.exit(0)

work = REPO / 'build/driver'
# Never reuse an old experiment output or silently overwrite an interrupted build.
if work.exists():
    sys.exit(f'Build workspace already exists: {work}. Move or remove it before a clean rebuild.')
work.mkdir(parents=True)
(work/'tmp').mkdir()
env = os.environ.copy()
env.update(PATH=str(clang/'bin')+os.pathsep+env['PATH'], TMPDIR=str(work/'tmp'))
def run(command, **kwargs):
    command = [str(x) for x in command]
    print('+', ' '.join(command), flush=True)
    subprocess.run(command, env=env, check=True, **kwargs)

def extract(archive, dest):
    dest.mkdir()
    # data filter rejects traversal and unsafe link targets in BYOB archives.
    with tarfile.open(archive) as tf:
        if not hasattr(tarfile, 'data_filter'):
            sys.exit('Python with tarfile.data_filter required (Python 3.12+ recommended)')
        tf.extractall(dest, filter='data')
    entries = list(dest.iterdir())
    if len(entries) != 1 or not entries[0].is_dir():
        sys.exit(f'Expected one top-level source directory in {archive}')
    return entries[0]

kernel = extract(kernel_archive, work/'kernel')
wlan = extract(wlan_archive, work/'wlan')
run([sys.executable, UPSTREAM/'tools/prepare_kernel_sources.py', '--kernel', kernel, '--wlan', wlan])
# Published baseline is framework mode. Apply the existing independent-mode conversion.
run(['patch', '--batch', '--fuzz=0', '-R', '-p1', '-d', wlan/'qcacld-3.0/core/hdd', '-i', UPSTREAM/'kernel/legacy-to-framework.patch'])
shutil.copytree(REPO/'driver/wlan', wlan, dirs_exist_ok=True)
for name in ('bridge', 'wonder'):
    shutil.copytree(UPSTREAM/'kernel'/name, work/name)
config_dir = UPSTREAM/'kernel/configs'
commands = json.loads((UPSTREAM/'kernel/build-commands.json').read_text())
outputs = {}
for name in ('bridge', 'wlan', 'wonder'):
    out = work/('out-'+name)
    out.mkdir()
    shutil.copyfile(config_dir/(name+'.config'), out/'.config')
    env['WONDER_SOURCE'] = str(work/'wonder')
    common = ['make', '-C', kernel, f'O={out}', 'ARCH=arm64', 'LLVM=1', 'LLVM_IAS=1', 'KERNELRELEASE=6.1.68', f'PAHOLE={shutil.which("pahole")}']
    if name == 'wonder':
        common.append(f'KBUILD_KCONFIG={config_dir}/Kconfig.wonder')
    run(common+['olddefconfig'])
    run(common+['-j4', 'modules_prepare'])
    shutil.copyfile(config_dir/'kernel.symvers', out/'Module.symvers')
    extra = work/'wlan-extra.symvers'
    if name == 'wlan':
        bridge = (work/'bridge/Module.symvers').read_text().splitlines()
        symbols = {x.split()[1] for x in bridge}
        baseline = (config_dir/'wlan-extra.symvers').read_text().splitlines()
        extra.write_text('\n'.join([x for x in baseline if x.split()[1] not in symbols]+bridge)+'\n')
    values = dict(KERNEL=kernel, KERNEL_OUT=out, WLAN=wlan, BRIDGE=work/'bridge', WONDER=work/'wonder', EXTRA_SYMVERS=extra, IPA_INCLUDE=UPSTREAM/'kernel/dataipa-include', EXTERNAL_INCLUDE=UPSTREAM/'kernel/external-include', PAHOLE=shutil.which('pahole'), WONDER_PROVIDERS=config_dir/'wonder-providers.symvers', KCONFIG_WONDER=config_dir/'Kconfig.wonder')
    command = commands[name]
    for key, value in values.items():
        command = [x.replace('${'+key+'}', str(value)) for x in command]
    run(command+['KERNELRELEASE=6.1.68'])
    source = wlan/'qcacld-3.0' if name == 'wlan' else work/name
    found = list(source.glob('*.ko'))
    if len(found) != 1:
        sys.exit(f'Expected one {name} module: {found}')
    outputs[name] = found[0]
# Publish only after all three modules compile; no release lock or checksum file.
dest = REPO/'dist/driver'
dest.mkdir(parents=True, exist_ok=True)
for source in outputs.values():
    shutil.copy2(source, dest/source.name)
print(f'DRIVER_BUILD_PASS: {dest}; this is a build result, not device/ABI acceptance.')
