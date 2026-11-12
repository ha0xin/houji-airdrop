from pathlib import Path
from samloader import fusclient,request
import xml.etree.ElementTree as ET
c=fusclient.FUSClient()
for region,csc in [('FWD','S928BOXM6EZIF'),('SWC','S928BOXM6EZIF')]:
 fw=f'S928BXXU6EZIF/{csc}/S928BXXU6EZIF/S928BXXU6EZIF'
 result=c.makereq('NF_DownloadBinaryInform.do',request.binaryinform(fw,'SM-S928B',region,c.nonce))
 Path(f'fus-{region}.xml').write_text(result)
 root=ET.fromstring(result)
 print(region,[(e.tag,e.text) for e in root.iter() if e.tag in ['Status','BINARY_NAME','BINARY_BYTE_SIZE','BINARY_MODEL_NAME']])
 print('values',[(e.tag,(e.findtext('Data') or '')[:150]) for e in root.findall('.//Put/*') if any(s in e.tag for s in ['BINARY','DEVICE_MODEL','DEVICE_FW'])])
