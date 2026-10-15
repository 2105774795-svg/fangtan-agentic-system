import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import store


class StoreMigrationTests(unittest.TestCase):
    def test_outline_background_columns_are_added_without_losing_legacy_jobs(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            db_path = folder / "legacy-outline.db"
            with closing(sqlite3.connect(db_path)) as db:
                with db:
                    db.executescript("""
                    CREATE TABLE outline_jobs(
                        id TEXT PRIMARY KEY, owner_id INTEGER NOT NULL, fingerprint TEXT NOT NULL,
                        params TEXT NOT NULL, status TEXT NOT NULL, questions TEXT NOT NULL DEFAULT '[]',
                        error TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, updated_at REAL NOT NULL
                    );
                    INSERT INTO outline_jobs VALUES('old',0,'old-fingerprint','{}','ready','["旧问题？"]','',1,1);
                    """)
            with patch.object(store, "DATA_DIR", folder), patch.object(store, "DB_PATH", db_path):
                store.init()
                old = store.get_outline_job('old')
                self.assertEqual(old['questions'], '["旧问题？"]')
                self.assertEqual(old['background'], '')
                self.assertEqual(old['background_note'], '')
                store.create_outline_job('new', 0, 'new-fingerprint', {})
                store.finish_outline_job('new', ['新问题？'], background='地点背景待核实。', background_note='采访前参考。')
                store.init()
                saved = store.get_outline_job('new')
                self.assertEqual(saved['background'], '地点背景待核实。')
                self.assertEqual(saved['background_note'], '采访前参考。')
                self.assertEqual(saved['status'], 'ready')

    def test_old_project_groups_become_owner_scoped_folders_idempotently(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            with patch.object(store, "DATA_DIR", folder), patch.object(store, "DB_PATH", folder / "legacy.db"):
                store.init()
                for sid, owner in (("one", 1), ("two", 1), ("other", 2), ("anonymous", None)):
                    store.create_session(sid, sid, "导入文字稿", "商户", [], owner_id=owner)
                    with store.get() as db:
                        db.execute("UPDATE sessions SET project_name='旧项目',project_id=NULL WHERE id=?", (sid,))
                store.init()
                first = store.get_session_summary("one")["project_id"]
                self.assertEqual(store.get_session_summary("two")["project_id"], first)
                self.assertNotEqual(store.get_session_summary("other")["project_id"], first)
                self.assertEqual(len(store.list_projects()), 3)
                store.init()
                self.assertEqual(store.get_session_summary("one")["project_id"], first)
                self.assertEqual(len(store.list_projects()), 3)

    def test_existing_speaker_result_keeps_its_job_version_after_upgrade(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            db_path = folder / "legacy.db"
            with closing(sqlite3.connect(db_path)) as db:
                with db:
                    db.executescript("""
                    CREATE TABLE sessions(
                        id TEXT PRIMARY KEY, subject TEXT, scene TEXT, interviewee_type TEXT,
                        industry TEXT DEFAULT '', focus TEXT DEFAULT '', outline TEXT,
                        status TEXT DEFAULT 'live', created_at REAL, finished_at REAL,
                        subject_source TEXT DEFAULT 'default', speaker_consented_at REAL,
                        speaker_job_id TEXT DEFAULT ''
                    );
                    CREATE TABLE speaker_segments(
                        id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
                        speaker_id INTEGER NOT NULL, start_ms INTEGER NOT NULL,
                        end_ms INTEGER NOT NULL, text TEXT NOT NULL
                    );
                    INSERT INTO sessions(id,status,created_at,speaker_consented_at,speaker_job_id)
                    VALUES('saved','done',1,1,'oldjob'),('unfinished','done',2,1,'newjob');
                    INSERT INTO speaker_segments(session_id,speaker_id,start_ms,end_ms,text)
                    VALUES('saved',0,0,1000,'旧结果');
                    """)
            with patch.object(store, "DATA_DIR", folder), patch.object(store, "DB_PATH", db_path):
                store.init()
                saved = store.get_session_summary("saved")
                unfinished = store.get_session_summary("unfinished")
            self.assertEqual(saved["speaker_result_job_id"], "oldjob")
            self.assertEqual(unfinished["speaker_result_job_id"], "")


if __name__ == "__main__":
    unittest.main()
