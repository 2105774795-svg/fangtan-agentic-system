import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import importer


class ImporterTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def write(self, name, content, encoding="utf-8"):
        path = self.root / name
        path.write_text(content, encoding=encoding)
        return path

    def test_txt_supports_chinese_legacy_encoding_and_paragraphs(self):
        path = self.write("访谈.txt", "第一段内容。\n\n第二段内容。", "gb18030")
        rows = importer.extract_utterances(path)
        self.assertEqual([row["text"] for row in rows], ["第一段内容。", "第二段内容。"])

    def test_srt_preserves_timestamps(self):
        path = self.write("sample.srt", "1\n00:00:01,250 --> 00:00:03,000\n主持人：请介绍一下。\n")
        rows = importer.extract_utterances(path)
        self.assertEqual(rows[0]["start_ms"], 1250)
        self.assertEqual(rows[0]["end_ms"], 3000)
        self.assertIn("主持人", rows[0]["text"])

    def test_csv_combines_speaker_and_content(self):
        path = self.write("sample.csv", "时间,说话人,内容\n00:01:02,张总,今年订单增加了\n")
        rows = importer.extract_utterances(path)
        self.assertEqual(rows[0]["start_ms"], 62000)
        self.assertEqual(rows[0]["text"], "张总：今年订单增加了")

    def test_json_accepts_common_segment_shape(self):
        payload = {"segments": [{"start": 2.5, "end": 4, "speaker": "受访者", "text": "毛利下降"}]}
        path = self.write("sample.json", json.dumps(payload, ensure_ascii=False))
        rows = importer.extract_utterances(path)
        self.assertEqual(rows[0], {"start_ms": 2500, "end_ms": 4000, "text": "受访者：毛利下降"})

    def test_docx_extracts_paragraphs_without_external_dependency(self):
        path = self.root / "sample.docx"
        document_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
        <w:p><w:r><w:t>第一位发言人</w:t></w:r></w:p>
        <w:p><w:r><w:t>第二段纪要</w:t></w:r></w:p>
        </w:body></w:document>"""
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("word/document.xml", document_xml)
        rows = importer.extract_utterances(path)
        self.assertEqual([row["text"] for row in rows], ["第一位发言人", "第二段纪要"])

    def test_empty_transcript_is_rejected(self):
        path = self.write("empty.md", " \n ")
        with self.assertRaisesRegex(importer.TranscriptFormatError, "没有可分析"):
            importer.extract_utterances(path)


if __name__ == "__main__":
    unittest.main()
