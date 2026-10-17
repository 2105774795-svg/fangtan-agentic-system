import type { Scenario, Scene } from './types';

export const interviewScenario: Scenario = {
  name: '面试准备', description: '围绕真实经历准备、练习与复盘', noun: '面试', fields: [],
  steps: ['目标确认', '背景与材料', '问题与策略', '模拟练习', '复盘改进', '成果归档'],
};
export const interviewStepKeys = ['goal', 'materials', 'questions', 'practice', 'review', 'archive'] as const;
export const sceneRegistry: Record<Scene, { captureLabel: string; assistantLabel: string; mapLabel: string; materialsLabel: string; insightsLabel: string; newProjectLabel: string }> = {
  market: { captureLabel: '开展访谈', assistantLabel: '研究助手', mapLabel: '调研路径', materialsLabel: '研究资料', insightsLabel: '用户洞察', newProjectLabel: '新建研究项目' },
  enterprise: { captureLabel: '开展访谈', assistantLabel: '研究助手', mapLabel: '调研路径', materialsLabel: '研究资料', insightsLabel: '研究洞察', newProjectLabel: '新建研究项目' },
  memoir: { captureLabel: '多次讲述', assistantLabel: '讲述对话', mapLabel: '讲述路径', materialsLabel: '讲述资料', insightsLabel: '讲述线索', newProjectLabel: '新建讲述项目' },
  interview: { captureLabel: '模拟练习', assistantLabel: '面试对话', mapLabel: '面试底图', materialsLabel: '项目资料', insightsLabel: '面试复盘', newProjectLabel: '新建项目' },
};
