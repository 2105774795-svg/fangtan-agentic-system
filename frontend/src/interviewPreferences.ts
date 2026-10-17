// Only explicit preferences change the operating defaults. Other requirements
// remain in the full goal sent to the preparation and interview agents.
export function interviewPreferences(text: string): Partial<{template:'job'|'thesis'; language:'中文'|'English'; duration_minutes:number; question_count:number; practice_mode:'simulation'|'coaching'}> {
  const minutes = text.match(/(\d+)\s*分钟/);
  const questions = text.match(/(?:练习|准备|问|做|答)\s*(\d+)\s*(?:道|个)?题/);
  return {
    ...(/论文|开题|毕业|硕士|博士/.test(text) ? {template:'thesis' as const} : /求职|应聘|公司|岗位|招聘/.test(text) ? {template:'job' as const} : {}),
    ...(/(?:中文|汉语)(?:回答|面试|练习)?/.test(text) ? {language:'中文' as const} : /英文|英语|English/i.test(text) && !/(?:不|别|无需|不用|不要).{0,3}(?:英文|英语|English)/i.test(text) ? {language:'English' as const} : {}),
    ...(minutes ? {duration_minutes:Math.max(3,Math.min(90,Number(minutes[1])))} : {}),
    ...(questions ? {question_count:Math.max(2,Math.min(12,Number(questions[1])))} : {}),
    ...(/结束后(?:再|统一)?(?:反馈|复盘)|模拟模式/.test(text) ? {practice_mode:'simulation' as const} : /每题反馈|辅导模式/.test(text) ? {practice_mode:'coaching' as const} : {}),
  };
}
