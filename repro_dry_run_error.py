import json
import traceback

from workflow_agent_service import agent_dry_run

wf = json.load(open('Latent_couple_api.json','r',encoding='utf-8'))

req = {
    'workflow': 'Latent_couple_api.json',
    'intent': 'noop',
    'ops': [
        {'op':'set','node':'11:742','input':'lora_name','value':'x'}
    ],
    'include_workflow': False,
}

try:
    print(agent_dry_run(wf, req, workflow_name=req['workflow']))
except Exception:
    traceback.print_exc()
