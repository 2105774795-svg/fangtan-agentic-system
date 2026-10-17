"""Explicit live Kimi smoke: synthetic data only, no daily database or recordings."""
import json
import sys
import tempfile
import os
import time
from pathlib import Path
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'backend'))
from interview_prep import ai
import config

def main():
    data = {
        'config': {'template':'job','goal':'合成演练：应聘产品经理，解释个人贡献和项目验证','language':'中文','practice_mode':'coaching','question_count':2,'max_followups':1,'duration_minutes':10},
        'materials': [{'id':'synthetic-resume','title':'合成简历（不是真实个人资料）','kind':'resume','version':1,'content':'虚构练习人物：负责一个内部预约流程的需求整理，访谈五名同事，制作原型并组织试用。没有提供效率提升数据。'}],
    }
    results = {'model': config.KIMI_MODEL, 'synthetic_only':True, 'asr_tested':False, 'checks':[]}
    try:
        started=time.monotonic()
        plan=ai.generate('interview_prepare',data)
        results['checks'].append({'kind':'prepare','valid':True,'seconds':round(time.monotonic()-started,2),'question_count':len(plan['questions'])})
        data['plan']=plan
        text='我负责整理预约流程中的需求，访谈了五名同事，然后制作原型并组织试用。目前还没有完整的效率对比数据。'
        data['turns']=[{'id':'synthetic-turn','question':plan['questions'][0]['question'],'target':plan['questions'][0]['target'],'status':'answered','parent_id':None,'answers':[{'id':'synthetic-answer','version':1,'transcript':text,'segments':[{'id':1,'start_ms':0,'end_ms':15000,'text':text}]}]}]
        for kind in ('interview_next_turn','interview_review'):
            started=time.monotonic()
            generated=ai.generate(kind,data)
            results['checks'].append({'kind':kind,'valid':True,'seconds':round(time.monotonic()-started,2),'action':generated.get('action'),'evidence_count':len(generated.get('feedback',generated.get('findings',[])))})
        results['passed']=True
    except Exception as exc:
        results['passed']=False
        results['error_type']=type(exc).__name__
        response=getattr(exc,'response',None)
        if response is not None:results['http_status']=response.status_code
        # Never emit raw provider response, request headers or exception details.
    out=root/'data'/'qa'/'interview';out.mkdir(parents=True,exist_ok=True)
    (out/'kimi-live-result.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(results,ensure_ascii=False))
    return 0 if results['passed'] else 1

if __name__=='__main__':raise SystemExit(main())
