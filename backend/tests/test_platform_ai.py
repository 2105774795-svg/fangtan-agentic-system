import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import platform_ai


class PlatformModelTests(unittest.TestCase):
    def test_research_analysis_preserves_evidence_and_distinguishes_hypotheses(self):
        class Context:
            def progress(self, *args): pass
        for scene in ['market', 'enterprise']:
            state = {'prompt': '研究需求', 'scenario': scene, 'answers': {}, 'context': '', 'messages': [],
                     'artifacts': [{'id': 'material', 'kind': 'material', 'title': '合成访谈', 'content': '我上周把记录复制到表格里。'}]}
            def generated(messages, on_text, **kwargs):
                instructions = messages[0]['content']
                self.assertIn('待验证假设', instructions)
                self.assertIn('不能直接写成普遍需求或已验证结论', instructions)
                self.assertIn('每个实质判断保留原文引用', instructions)
                material = json.loads(messages[1]['content'])
                self.assertEqual(material['原始材料'][0]['paragraphs'][0]['ref'], 'M1-P1')
                return '研究发现：存在手动整理记录的行为 [M1-P1]。需求机会待进一步验证。'
            with patch('platform_ai.stream', side_effect=generated):
                result = platform_ai.generate('analysis', state, Context(), lambda _: None)
            self.assertEqual(result['source_ids'], ['material'])

    def test_memoir_keeps_story_language_instead_of_product_demand_framework(self):
        class Context:
            def progress(self, *args): pass
        state = {'prompt': '讲述故乡', 'scenario': 'memoir', 'answers': {}, 'context': '', 'messages': [], 'artifacts': []}
        def generated(messages, on_text, **kwargs):
            self.assertIn('尊重记忆不确定性的开放问题', messages[0]['content'])
            self.assertNotIn('目标人群与招募标准', messages[0]['content'])
            self.assertNotIn(platform_ai.RESEARCH_LANGUAGE, messages[0]['content'])
            return '- 您记忆中的故乡是什么样的？'
        with patch('platform_ai.stream', side_effect=generated):
            platform_ai.generate('outline', state, Context(), lambda _: None)

    def test_integrated_report_uses_template_and_both_interview_sources(self):
        state = {"prompt": "合成市场调研", "scenario": "market", "answers": {}, "context": "", "messages": [],
                 "interview_inventory": [{"id": "one", "subject": "张店主初访", "number": 1}, {"id": "two", "subject": "张店主补访", "number": 2}],
                 "artifacts": [{"id": "m1", "title": "初访", "kind": "material", "source_session": "one", "content": "需要遮雨棚"},
                               {"id": "m2", "title": "补访", "kind": "material", "source_session": "two", "content": "冷库足够"}]}
        class Context:
            def progress(self, *args): pass
        def generated(messages, on_text, **kwargs):
            self.assertIn("执行摘要", messages[0]["content"])
            self.assertIn("六 关键分歧与研究风险", messages[0]["content"])
            material = json.loads(messages[1]["content"])
            self.assertEqual(material["原始材料"][1]["interview"]["subject"], "张店主补访")
            self.assertIn("不等于独立受访人数", material["统计口径"])
            return "# 合成整合报告\n\n需遮雨棚 [M1-P1]；冷库足够 [M2-P1]。"
        with patch("platform_ai.stream", side_effect=generated):
            result = platform_ai.generate("report", state, Context(), lambda _: None)
        self.assertEqual(result["source_ids"], ["m1", "m2"])
        with patch("platform_ai.stream", return_value="只引用初访 [M1-P1]"), self.assertRaises(ValueError):
            platform_ai.generate("report", state, Context(), lambda _: None)

    def client(self, frames, status=200):
        content = "\n\n".join("data: " + (json.dumps(frame) if isinstance(frame, dict) else frame) for frame in frames)
        return httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(status, content=content.encode(), request=request)))

    def test_stream_ignores_reasoning_and_accumulates_actual_text(self):
        frames = [{"choices": [{"delta": {"reasoning_content": "private reasoning"}}]}, {"choices": [{"delta": {"content": "真实"}}]}, {"choices": [{"delta": {"content": "回复"}, "finish_reason": "stop"}]}, "[DONE]"]
        client, pieces = self.client(frames), []
        with patch.object(platform_ai.config, "KIMI_API_KEY", "test"), patch("platform_ai.httpx.Client", return_value=client):
            result = platform_ai.stream([], pieces.append)
        self.assertEqual(result, "真实回复")
        self.assertEqual(pieces, ["真实", "回复"])

    def test_truncated_stream_never_becomes_success(self):
        for frames in ([{"choices": [{"delta": {"content": "半句"}}]}], [{"choices": [{"delta": {"content": "半句"}, "finish_reason": "length"}]}, "[DONE]"]):
            client = self.client(frames)
            with patch.object(platform_ai.config, "KIMI_API_KEY", "test"), patch("platform_ai.httpx.Client", return_value=client), self.assertRaises(ValueError):
                platform_ai.stream([], lambda _: None)

    def test_stream_collects_tool_arguments_without_exposing_json(self):
        frames = [{"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "create_document", "arguments": '{"target_'}}]}}]},
                  {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'step":1}'}}]}, "finish_reason": "tool_calls"}]}, "[DONE]"]
        client, pieces, calls = self.client(frames), [], []
        with patch.object(platform_ai.config, "KIMI_API_KEY", "test"), patch("platform_ai.httpx.Client", return_value=client):
            result = platform_ai.stream([], pieces.append, tools=[{"type": "function"}], tool_calls=calls)
        self.assertEqual(result, "")
        self.assertEqual(pieces, [])
        self.assertEqual(calls, [{"name": "create_document", "arguments": '{"target_step":1}'}])

    def test_document_tool_saves_the_requested_existing_reply(self):
        state = {"prompt": "锦州调研", "scenario": "market", "stage": 3, "answers": {}, "context": "", "artifacts": [],
                 "messages": [{"id": "reply", "role": "assistant", "text": "# 访谈行程安排\n\n建议上午访谈，具体时间待确认。"}, {"id": "ask", "role": "user", "text": "把上面作为新文档放到背景与计划"}]}
        class Context:
            def progress(self, *args):
                pass
        def select_tool(messages, on_text, **kwargs):
            self.assertEqual(kwargs["tools"][0]["function"]["name"], "create_document")
            kwargs["tool_calls"].append({"name": "create_document", "arguments": json.dumps({"target_step": 1, "title": "访谈行程安排", "brief": "保存上一条行程", "content_source": "previous_reply", "source_message_id": "reply"})})
            return ""
        output = []
        with patch("platform_ai.stream", side_effect=select_tool):
            result = platform_ai.generate("chat", state, Context(), output.append)
        self.assertEqual(result["document"], {"step": 1, "title": "访谈行程安排"})
        self.assertEqual(result["content"], state["messages"][0]["text"])
        self.assertEqual(output, [result["content"]])
        with self.assertRaises(ValueError):
            platform_ai.create_document({"target_step": 5, "title": "未来模块", "brief": "任务", "content_source": "generate"}, state, {}, Context(), output.append)

    def test_unknown_material_reference_is_rejected(self):
        state = {"prompt": "访谈", "scenario": "market", "answers": {}, "context": "", "messages": [], "artifacts": [{"id": "one", "title": "原文", "kind": "material", "content": "原始发言"}]}
        class Context:
            def progress(self, *args):
                pass
        with patch("platform_ai.stream", return_value="结论 [M1-P999]"), self.assertRaises(ValueError):
            platform_ai.generate("analysis", state, Context(), lambda _: None)

    def test_http_errors_are_sanitized(self):
        response = httpx.Response(401, request=httpx.Request("POST", "https://example.com"))
        error = httpx.HTTPStatusError("secret credential", request=response.request, response=response)
        self.assertNotIn("secret", platform_ai.safe_error(error))
        self.assertIn("鉴权", platform_ai.safe_error(error))

    def test_intake_uses_real_stream_and_one_authoritative_checklist(self):
        from research_platform import SCENARIOS, intake_fields
        state = {"prompt": "锦州市场调研", "scenario": "market", "scenario_confirmed": True, "answers": {}}
        class Context:
            def progress(self, *args):
                pass
        pieces = []
        def streamed(messages, on_text, **kwargs):
            self.assertIn(SCENARIOS["market"]["fields"][0]["label"], messages[-1]["content"])
            on_text("已理解锦州")
            on_text("市场调研。")
            return "已理解锦州市场调研。"
        metadata = {"scenario": "enterprise", "title": "锦州调研", "reply": "metadata only", "location": {"city": "锦州"}}
        with patch("platform_ai.kimi._chat", return_value=json.dumps(metadata)), patch("platform_ai.stream", side_effect=streamed):
            result = platform_ai.generate("intake", state, Context(), pieces.append)["intake"]
        self.assertEqual(pieces, ["已理解锦州", "市场调研。"])
        self.assertEqual(result["reply"], "".join(pieces))
        self.assertEqual(result["scenario"], "market")
        self.assertEqual(result["checklist"], intake_fields(state))


if __name__ == "__main__":
    unittest.main()
