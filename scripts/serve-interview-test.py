"""Explicit isolated browser fixture: synthetic materials, ASR/model; localhost only."""
import os
import sys
import tempfile
import threading
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
_data=tempfile.TemporaryDirectory(prefix='interview-browser-')
os.environ['FANGTAN_DATA_DIR']=_data.name
os.environ['FANGTAN_AUTH_ENABLED']='false'
os.environ['KIMI_API_KEY']=''
os.environ['DASHSCOPE_API_KEY']=''
os.environ['TEAMFLOW_USAGE_URL']=''
import app
import config
import worker
from interview_prep import ai

def fixture(kind,data):
    if kind=='interview_question_analysis':
        return {'intent':'理解候选人对岗位的认知和真实动机。','answer_outline':['说明公司业务理解','连接自己的相关经历'],
                'previous_answer_feedback':['旧回答只表达认可，缺少具体匹配证据'],
                'followup_angles':['为什么选择这个岗位而不是其他方向'],
                'preparation_actions':['准备一段与岗位相关的真实项目'],'missing_information':[]}
    if kind=='interview_prepare':
        return {'summary':'根据你的合成简历，重点练习个人贡献与结果依据。','preparation_tips':['说明个人行动和验证方法'],'questions':[{'question':'请介绍你负责的流程改进项目？','target':'个人贡献'},{'question':'项目中最重要的取舍是什么？','target':'决策依据'}]}
    answer=[a for t in data['turns'] for a in t['answers']][-1]
    finding={'dimension':'个人贡献','observation':'你已说明个人行动，可继续补充结果的验证方法。','suggestion':'说明使用了什么记录或测试来验证结果。','answer_id':answer['id'],'quote':answer['transcript'][:12],'segment_ids':[]}
    closing=any(t.get('turn_kind')=='candidate_questions' and t.get('answers') for t in data.get('turns',[]))
    candidate_review={'summary':'问题聚焦岗位目标，有助于双向选择。','strengths':['关注入职预期'],
        'improvements':['可以继续问成功标准及协作方式'],
        'recommended_questions':[{'question':'团队会如何评价这个岗位前三个月的成果？','why':'了解成功标准，便于判断匹配度','timing':'业务主管面试收尾'}]}
    if kind=='interview_next_turn' and closing:
        return {'action':'finish','feedback':[finding],'candidate_questions_review':candidate_review}
    if kind=='interview_next_turn':return {'action':'follow_up','follow_up_reason':'刚才回答未说明验证方法，需要了解具体依据','question':{'question':'你刚才提到的结果是怎么验证的？','target':'结果依据'},'feedback':[finding]}
    return {'summary':'本轮回答说明了行动，下一次可以更具体地解释证据。','findings':[finding],'retry_questions':[{'question':'请用一个具体证据说明项目结果？','target':'结果依据'}], 'candidate_questions_review':candidate_review if closing else None}

config.KIMI_API_KEY='fixture-only'
config.DASHSCOPE_API_KEY='fixture-only'
ai.generate=fixture
app.asr_dashscope.transcribe=lambda path: '我负责梳理需求并设计了新的流程，随后组织测试验证结果。'
_stop=threading.Event()
def work():
    while not _stop.is_set():
        if not worker.run_once(handlers=ai.HANDLERS,worker_id='isolated-browser'):_stop.wait(.1)
threading.Thread(target=work,daemon=True).start()
if __name__=='__main__':
    import uvicorn
    try:uvicorn.run(app.app,host='127.0.0.1',port=19001,log_level='warning')
    finally:_stop.set()
