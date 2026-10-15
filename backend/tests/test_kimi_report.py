import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import kimi


class ReportGenerationTests(unittest.TestCase):
    @patch("kimi._chat", return_value="# 访谈纪要：测试")
    def test_imported_transcript_uses_paragraph_markers(self, chat):
        transcript = [{"start_ms": 60000, "end_ms": 90000, "text": "测试内容"}]
        kimi.gen_report("测试", "企业高管", transcript, source_kind="导入文字稿")
        prompt = chat.call_args.args[0][1]["content"]
        self.assertIn("[段落01] 测试内容", prompt)
        self.assertNotIn("[01:00]", prompt)

    @patch("kimi._chat", return_value="# 访谈纪要：字幕")
    def test_subtitle_transcript_keeps_real_timestamp_markers(self, chat):
        transcript = [{"start_ms": 62500, "end_ms": 70000, "text": "字幕内容"}]
        kimi.gen_report("字幕", "消费者", transcript, source_kind="导入字幕稿")
        prompt = chat.call_args.args[0][1]["content"]
        self.assertIn("[01:02] 字幕内容", prompt)

    @patch("kimi._chat")
    def test_long_transcript_is_summarized_in_chunks_before_final_report(self, chat):
        chat.side_effect = lambda messages, **kwargs: "中间提取" if "中间材料" in messages[0]["content"] else "最终纪要"
        transcript = [
            {"start_ms": index * 1000, "end_ms": index * 1000 + 500, "text": "一段较长的访谈内容" * 250}
            for index in range(20)
        ]
        report = kimi.gen_report("长访谈", "行业专家", transcript, source_kind="导入音频")
        self.assertEqual(report, "最终纪要")
        self.assertGreater(chat.call_count, 2)
        final_prompt = chat.call_args.args[0][1]["content"]
        self.assertIn("分段事实提取", final_prompt)


if __name__ == "__main__":
    unittest.main()
