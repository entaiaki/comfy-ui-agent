import json
import traceback
from workflow_self_check import self_check_workflow_edit

wf = json.load(open('Latent_couple_api.json','r',encoding='utf-8'))

try:
    res = self_check_workflow_edit(
        wf,
        ops=[{'op':'set','node':'11:742','input':'lora_name','value':'x'}],
        text='',
        strict=False,
    )
    print('ok', res.get('valid'))
except Exception:
    traceback.print_exc()
