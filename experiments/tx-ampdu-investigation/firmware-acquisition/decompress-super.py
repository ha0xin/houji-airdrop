import lz4.frame,shutil,hashlib
from pathlib import Path
assert Path("AP-verified.json").exists(), "AP CRC verification required"
src=Path("super.img.lz4")
size=lz4.frame.get_frame_info(src.open("rb").read(32))["content_size"]
assert shutil.disk_usage(".").free > size+10*1024**3
h=hashlib.sha256();total=0
with lz4.frame.open(src,"rb") as f,open("super.img","wb") as out:
 while True:
  b=f.read(4*1024*1024)
  if not b:break
  h.update(b);out.write(b);total+=len(b)
assert total==size
Path("super.img.sha256").write_text(h.hexdigest()+"  super.img\n")
print("SPARSE_SUPER",total,h.hexdigest(),flush=True)
