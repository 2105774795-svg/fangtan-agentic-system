"""Explicit paid-model smoke using only invented interview materials; no ASR or DB."""
import json
import sys
import time
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'backend'))
from interview_prep import ai
import config

def main():
    cfg={'template':'job','goal':'合成产品经理面试练习','language':'中文','practice_mode':'coaching','question_count':2,
         'followup_mode':'adaptive','include_candidate_questions':True,'duration_minutes':15,'max_followups':1,
         'company_name':'虚构预约产品公司','role_name':'产品经理','company_background':'虚构练习设定：提供企业预约产品。',
         'job_requirements':'需要用户访谈、需求分析与试用验证经验。','interview_stage':'业务主管面试','background_source':'纯合成练习，不是真实企业'}
    question={'id':'synthetic-question','question':'你为什么选择我们公司？','previous_answer':'因为贵公司很优秀。','context':'上次模拟面试'}
    data={'config':cfg,'materials':[{'id':'synthetic-cv','kind':'resume','title':'虚构简历','version':1,'content':'虚构候选人整理需求、访谈五位同事并制作预约流程原型。'}],'question_bank':[question]}
    result={'synthetic_only':True,'model':config.KIMI_MODEL,'checks':[]}
    try:
        started=time.monotonic();plan=ai.generate('interview_prepare',data)
        assert plan['questions'][0]['question']==question['question']
        result['checks'].append({'kind':'company_role_plan','valid':True,'seconds':round(time.monotonic()-started,2),'company_focus':len(plan['company_and_role_focus']),'closing_suggestions':len(plan['candidate_question_suggestions'])})
        started=time.monotonic();analysis=ai.generate('interview_question_analysis',{**data,'historical_question':question})
        result['checks'].append({'kind':'historical_question_analysis','valid':bool(analysis['intent']),'seconds':round(time.monotonic()-started,2)})
        data['plan']=plan
        text='我了解的是你们提供预约产品。我之前访谈过五位同事并制作流程原型，想把需求分析经验用在这个岗位。目前我还需要了解团队的具体目标。'
        data['turns']=[{'id':'synthetic-turn','turn_kind':'question','question':question['question'],'status':'answered','parent_id':None,'answers':[{'id':'synthetic-answer','transcript':text,'version':1,'segments':[{'id':1,'start_ms':0,'end_ms':20000,'text':text}]}]}]
        started=time.monotonic();decision=ai.generate('interview_next_turn',data)
        result['checks'].append({'kind':'adaptive_followup','valid':True,'action':decision['action'],'has_reason':bool(decision.get('follow_up_reason')),'seconds':round(time.monotonic()-started,2)})
        text='这个岗位入职前三个月最重要的目标是什么？团队会如何判断我做得好不好？'
        data['turns'].append({'id':'synthetic-closing','turn_kind':'candidate_questions','question':'你有什么想向我们了解的？','status':'answered','parent_id':None,'answers':[{'id':'synthetic-closing-answer','transcript':text,'version':1,'segments':[{'id':2,'start_ms':0,'end_ms':15000,'text':text}]}]})
        started=time.monotonic();closing=ai.generate('interview_next_turn',data)
        assert closing.get('candidate_questions_review')
        result['checks'].append({'kind':'candidate_question_review','valid':True,'recommended_questions':len(closing['candidate_questions_review']['recommended_questions']),'seconds':round(time.monotonic()-started,2)})
        result['passed']=True
    except Exception as exc:
        result['passed']=False;result['error_type']=type(exc).__name__
        if getattr(exc,'response',None) is not None:result['http_status']=exc.response.status_code
    out=root/'data/qa/interview-v2';out.mkdir(parents=True,exist_ok=True)
    (out/'live-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))
    return 0 if result['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
