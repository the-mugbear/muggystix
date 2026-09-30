"""Local-only acceptance stub. It deliberately never opens a network socket."""
import json,hashlib,datetime
from pathlib import Path
root=Path(__file__).resolve().parent
manifest=json.loads((root/'targets.json').read_text())
output={'schema':'acceptance.custom-validator.v1','tool':'acceptance-custom-validator','tool_version':'1.0','synthetic':True,'network_traffic':False,'observed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'manifest_sha256':hashlib.sha256((root/'targets.json').read_bytes()).hexdigest(),'results':[{'host_id':t['host_id'],'ip_address':t['ip_address'],'outcome':'not_tested','reason':'Local manifest validation only; no target testing performed'} for t in manifest['targets']]}
(root/'custom-results.json').write_text(json.dumps(output,indent=2))
print('Validated',len(output['results']),'manifest entries locally; target checks not performed.')
