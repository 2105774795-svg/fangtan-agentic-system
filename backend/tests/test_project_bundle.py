import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import project_bundle
import store


class ProjectBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="project-bundle-tests-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for setting in (patch.object(store, "DB_PATH", self.root / "test.db"),
                        patch.object(store, "DATA_DIR", self.root),
                        patch.object(config, "DATA_DIR", self.root),
                        patch.object(config, "AUTH_ENABLED", True)):
            setting.start()
            self.addCleanup(setting.stop)
        store.init()
        self.project = store.create_project("重庆访谈", 1)

    def session(self, sid="one", *, owner=1, title="同名访谈", report="# 纪要\n保存全部内容", audio=True, finished=True):
        store.create_session(sid, title, "现场录音", "商户", [], owner_id=owner)
        store.assign_session_project(sid, self.project["id"])
        store.add_utterance(sid, 0, 1234, 6789, "原文😀\n第二行", speaker="受访者（手工）")
        if report is not None:
            store.save_report(sid, report)
        if finished:
            store.finish_session(sid)
        if audio:
            folder = self.root / "recordings" / sid
            folder.mkdir(parents=True)
            recording = folder / "original.MP3"
            recording.write_bytes(b"original-audio-" + sid.encode())
            store.set_audio_path(sid, str(recording))
        return sid

    def bundle(self, user_id=1, **kwargs):
        artifact = project_bundle.build_bundle(self.project["id"], user_id, **kwargs)
        self.addCleanup(artifact.cleanup)
        return artifact

    def manifest(self, archive):
        name = next(name for name in archive.namelist() if name.endswith("/manifest.json"))
        return json.loads(archive.read(name))

    def assert_no_temporary_zip(self):
        root = self.root / "exports"
        self.assertEqual(list(root.iterdir()) if root.exists() else [], [])

    def test_complete_audio_minutes_and_more_than_500_utterances(self):
        self.session()
        with store.get() as db:
            db.executemany("""INSERT INTO utterances(session_id,seq,start_ms,end_ms,text,created_at,speaker)
                VALUES('one',?,?,?,?,1,?)""", [(i, i * 1000 + 5, i * 1000 + 999, f"完整段落{i}", "员工甲") for i in range(1, 701)])
        artifact = self.bundle()
        self.assertEqual(artifact.session_count, 1)
        with ZipFile(artifact.path) as archive:
            names = archive.namelist()
            audio = next(name for name in names if name.endswith("/录音.mp3"))
            self.assertEqual(archive.read(audio), b"original-audio-one")
            rows = [json.loads(line) for line in archive.read(next(name for name in names if name.endswith(".jsonl"))).decode().splitlines()]
            self.assertEqual(len(rows), 701)
            self.assertEqual(rows[0], {"seq": 0, "start_ms": 1234, "end_ms": 6789, "speaker": "受访者（手工）", "text": "原文😀\n第二行"})
            self.assertEqual(rows[-1]["text"], "完整段落700")
            plain = archive.read(next(name for name in names if name.endswith("/完整逐字稿.txt"))).decode()
            self.assertIn("start_ms=1234 end_ms=6789", plain)
            self.assertIn("受访者（手工）：原文😀\n第二行", plain)
            self.assertIn("完整段落700", plain)
            self.assertIn("保存全部内容", archive.read(next(name for name in names if name.endswith(".md"))).decode())
            self.assertTrue(any(name.endswith(".docx") for name in names))
            self.assertEqual(self.manifest(archive)["warnings"], [])
        self.assertTrue((self.root / "recordings" / "one" / "original.MP3").is_file())

    def test_names_duplicates_emoji_and_traversal_are_safe(self):
        store.rename_project(self.project["id"], '../../CON\r\n:😀\\测试')
        self.session("one", title="../相同😀\n标题\\")
        self.session("two", title="../相同😀\n标题\\")
        artifact = self.bundle()
        self.assertNotIn("\r", artifact.filename)
        self.assertNotIn("\n", artifact.filename)
        self.assertNotIn("/", artifact.filename)
        self.assertIn("😀", artifact.filename)
        with ZipFile(artifact.path) as archive:
            names = archive.namelist()
            self.assertEqual(len(names), len(set(names)))
            for name in names:
                self.assertFalse(name.startswith(("/", "\\")))
                self.assertNotIn("..", name.split("/"))
                self.assertNotIn("\n", name)
                self.assertNotIn("\r", name)
            manifest = self.manifest(archive)
            self.assertEqual({item["id"] for item in manifest["sessions"]}, {"one", "two"})
            self.assertEqual(len({item["folder"] for item in manifest["sessions"]}), 2)

    def test_shared_project_contains_only_currently_visible_interviews(self):
        self.session("private", title="秘密访谈")
        self.session("shared", title="只读共享访谈")
        store.add_share("shared", 2, "员工2")
        artifact = self.bundle(user_id=2)
        with ZipFile(artifact.path) as archive:
            self.assertEqual([item["id"] for item in self.manifest(archive)["sessions"]], ["shared"])
            self.assertFalse(any("private" in name or "秘密" in name for name in archive.namelist()))
        with self.assertRaises(project_bundle.BundleError) as raised:
            self.bundle(user_id=3)
        self.assertEqual(raised.exception.status_code, 404)

    def test_missing_materials_are_explained_without_fake_files(self):
        self.session(audio=False, report=None)
        artifact = self.bundle()
        with ZipFile(artifact.path) as archive:
            manifest = self.manifest(archive)
            self.assertEqual(len(manifest["warnings"]), 1)
            missing = manifest["sessions"][0]["missing"]
            self.assertTrue(any("音频" in value for value in missing))
            self.assertTrue(any("纪要" in value for value in missing))
            notes = archive.read(next(name for name in archive.namelist() if name.endswith("/说明.txt"))).decode()
            self.assertIn("未加密", notes)
            self.assertIn("纪要尚未生成", notes)
            self.assertFalse(any(name.endswith(".docx") for name in archive.namelist()))

    def test_empty_live_and_unassembled_sessions_rejected(self):
        with self.assertRaises(project_bundle.BundleError) as raised:
            self.bundle()
        self.assertEqual(raised.exception.status_code, 400)
        self.session(finished=False)
        with self.assertRaises(project_bundle.BundleError) as raised:
            self.bundle()
        self.assertEqual(raised.exception.status_code, 409)
        store.finish_session("one")
        store.set_audio_path("one", "")
        with self.assertRaises(project_bundle.BundleError) as raised:
            self.bundle()
        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("未合成", raised.exception.detail)

    def test_outside_relative_and_link_paths_rejected(self):
        self.session()
        outside = self.root / "outside.mp3"
        outside.write_bytes(b"private-other-file")
        for path in (str(outside), "recordings/one/original.MP3", str(self.root / "recordings" / "one" / ".." / ".." / "outside.mp3")):
            store.set_audio_path("one", path)
            with self.subTest(path=path), self.assertRaises(project_bundle.BundleError) as raised:
                self.bundle()
            self.assertEqual(raised.exception.status_code, 409)
        link = self.root / "recordings" / "one" / "linked.mp3"
        try:
            link.symlink_to(outside)
        except OSError:
            # Windows 非管理员会禁止创建软链接；仍验证 lstat 识别链接的关卡。
            from types import SimpleNamespace
            link.write_bytes(b"dummy")
            original = Path.lstat
            def linked_stat(path, *args, **kwargs):
                if path == link:
                    return SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_file_attributes=0)
                return original(path, *args, **kwargs)
            link_patch = patch.object(Path, "lstat", autospec=True, side_effect=linked_stat)
            link_patch.start()
            self.addCleanup(link_patch.stop)
        store.set_audio_path("one", str(link))
        with self.assertRaises(project_bundle.BundleError) as raised:
            self.bundle()
        self.assertEqual(raised.exception.status_code, 409)
        self.assert_no_temporary_zip()

    def test_revoke_delete_move_and_text_edit_during_build_remove_temporary_zip(self):
        for change in ("revoke", "delete", "move", "text", "rename", "audio"):
            with self.subTest(change=change):
                store.delete_session_records("one")
                folder = self.root / "recordings" / "one"
                if folder.exists():
                    (folder / "original.MP3").unlink(missing_ok=True)
                    folder.rmdir()
                self.session()
                store.add_share("one", 2, "员工2")
                render = project_bundle.word_export.render_minutes
                def mutate(*args, **kwargs):
                    result = render(*args, **kwargs)
                    with store.get() as db:
                        if change == "revoke":
                            db.execute("DELETE FROM session_shares WHERE session_id='one' AND user_id=2")
                        elif change == "delete":
                            db.execute("DELETE FROM sessions WHERE id='one'")
                        elif change == "move":
                            db.execute("UPDATE sessions SET project_id=NULL WHERE id='one'")
                        elif change == "text":
                            db.execute("UPDATE utterances SET text='已改写' WHERE session_id='one'")
                        elif change == "rename":
                            db.execute("UPDATE projects SET name='另一个项目名' WHERE id=?", (self.project["id"],))
                        else:
                            (self.root / "recordings" / "one" / "original.MP3").write_bytes(b"changed-audio")
                    return result
                with patch("project_bundle.word_export.render_minutes", side_effect=mutate), self.assertRaises(project_bundle.BundleError) as raised:
                    self.bundle(user_id=2)
                self.assertEqual(raised.exception.status_code, 409)
                self.assert_no_temporary_zip()

    def test_post_build_verify_detects_revoke_and_cleans_up(self):
        self.session()
        store.add_share("one", 2, "员工2")
        artifact = self.bundle(user_id=2)
        with store.get() as db:
            db.execute("DELETE FROM session_shares WHERE session_id='one' AND user_id=2")
        with self.assertRaises(project_bundle.BundleError) as raised:
            artifact.verify()
        self.assertEqual(raised.exception.status_code, 409)
        self.assertFalse(artifact.path.exists())
        self.assertFalse(artifact.path.parent.exists())

    def test_limits_disk_space_write_failure_and_permissions(self):
        self.session()
        self.session("two")
        for options in ({"max_sessions": 1}, {"max_source_bytes": 5}, {"max_text_chars": 3}):
            with self.subTest(options=options), self.assertRaises(project_bundle.BundleError) as raised:
                self.bundle(**options)
            self.assertEqual(raised.exception.status_code, 413)
        with patch("project_bundle.shutil.disk_usage", return_value=shutil_usage(100, 99, 1)), self.assertRaises(project_bundle.BundleError) as raised:
            self.bundle()
        self.assertEqual(raised.exception.status_code, 507)
        with patch("project_bundle.word_export.render_minutes", side_effect=OSError("模拟写入失败")), self.assertRaises(project_bundle.BundleError) as raised:
            self.bundle()
        self.assertEqual(raised.exception.status_code, 409)
        self.assert_no_temporary_zip()
        artifact = self.bundle()
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(artifact.path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(artifact.path.parent.stat().st_mode), 0o700)
        artifact.cleanup()
        artifact.cleanup()
        self.assert_no_temporary_zip()


def shutil_usage(total, used, free):
    from collections import namedtuple
    return namedtuple("usage", "total used free")(total, used, free)


if __name__ == "__main__":
    unittest.main()
