// 旧链接由 App 解析到统一项目；专业面板只维护一份实现。
export type { Preparation } from './interview/InterviewPanels';
export async function prepApi<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api/interview-prep${path}`, options);
  if (!response.ok) { const body = await response.json().catch(() => ({})); throw new Error(typeof body.detail === 'string' ? body.detail : '面试资料暂时无法读取'); }
  return response.json();
}
