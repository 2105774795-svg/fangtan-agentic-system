"""人生传记的增量写作与审校回归；全部使用合成原话和模型响应。"""
import json
import unittest
from unittest.mock import patch

import test_memoir_agent as agent_tests
import store
import worker
from memoir_editor import chapters, biography
from agents.context import build


class BiographyTests(unittest.TestCase):
    setUp = agent_tests.MemoirAgentTests.setUp
    send = agent_tests.MemoirAgentTests.send
    run_reply = agent_tests.MemoirAgentTests.run_reply
    human = agent_tests.MemoirAgentTests.human

    def talk(self, text, key):
        self.assertEqual(self.send(text, key).status_code, 200)
        self.run_reply()

    def model(self, system, value):
        if system == chapters.SCENE_SYSTEM:
            scenes = []
            for t in value['human_turns']:
                title = '走进工厂' if '厂' in t['text'] else '小时候的家'
                old = next((s for s in value['related_scenes'] if s['title'] == title), None)
                scenes.append({'existing_id': old['id'] if old else '', 'title': title, 'summary': t['text'][:500],
                               'missing': ['您还记得那里的样子吗？'], 'evidence': [{'turn_id': t['turn_id'], 'quote': t['text'][:1000]}]})
            return {'scenes': scenes, 'deferred_turn_ids': []}
        if system == chapters.PLAN_SYSTEM:
            return {'chapters': [{'chapter_id': next((c['id'] for c in value['existing_chapters'] if c['title'] == s['title']), ''),
                                  'title': s['title'], 'scene_ids': [s['id']]} for s in value['new_scenes']]}
        if system in {chapters.AUDIT_SYSTEM, biography.CONTINUITY_SYSTEM}: return {'issues': []}
        return {'paragraphs': [{'text': t['text'], 'evidence': [{'turn_id': t['turn_id'], 'quote': t['text'][:1000]}]} for t in value['human_turns']]}

    def write(self, mode='continue', model=None):
        response = self.client.post(self.base + '/conversation-book', json={'mode': mode})
        self.assertEqual(response.status_code, 200, response.text)
        with patch('memoir_editor.chapters._chat', side_effect=model or self.model) as calls:
            worker.run_once(handlers={'conversation_book': chapters.job})
        value = self.client.get(self.base + '/conversation-book').json()
        return value, calls

    def prefs(self, **body):
        revision = self.client.get(self.base + '/conversation-book').json()['settings']['revision']
        return self.client.post(self.base + '/conversation-book/settings', json={'revision': revision, **body})

    def review(self, **body):
        revision = self.client.get(self.base + '/conversation-book').json()['book']['revision']
        return self.client.post(self.base + '/conversation-book/review', json={'revision': revision, **body})

    def test_sample_then_approval_then_incremental_chapters_and_stable_ids(self):
        self.talk('小时候我和母亲住在村里。', 'childhood-01')
        self.talk('十六岁我进了工厂，师傅姓李。', 'factory-01')
        first, _ = self.write()
        self.assertEqual(first['book']['stage'], 'sample')
        self.assertEqual(len(first['book']['chapters']), 1)
        self.assertEqual(len(first['book']['outline']), 2)
        self.assertIn('《从文自传》', first['settings']['style']['name'])
        self.assertEqual(self.prefs(action='approve_style').status_code, 200)
        second, calls = self.write()
        self.assertEqual(len(second['book']['chapters']), 2)
        self.assertEqual(first['book']['chapters'][0], second['book']['chapters'][0])
        self.assertEqual(sum(c.args[0] == biography.WRITER_SYSTEM for c in calls.call_args_list), 1)
        self.assertEqual(first['book']['outline'], second['book']['outline'])
        third, calls = self.write(model=lambda *_: self.fail('未变化的材料不应再次调用模型'))
        self.assertEqual(third['job']['status'], 'ready')
        self.assertEqual(calls.call_count, 0)

    def test_new_information_changes_only_related_chapter_and_preserves_lock(self):
        self.talk('小时候我和母亲住在村里。', 'childhood-01')
        self.talk('十六岁我进了工厂，师傅姓李。', 'factory-01')
        first, _ = self.write(); self.prefs(action='approve_style'); first, _ = self.write()
        factory = next(c for c in first['book']['chapters'] if c['title'] == '走进工厂')
        childhood = next(c for c in first['book']['chapters'] if c['title'] != '走进工厂')
        self.assertEqual(self.review(action='lock_chapter', chapter_id=factory['id']).status_code, 200)
        self.talk('厂里第一天，李师傅让我先扫地。', 'factory-02')
        updated, calls = self.write()
        kept = next(c for c in updated['book']['chapters'] if c['id'] == factory['id'])
        self.assertEqual(kept['paragraphs'], factory['paragraphs'])
        self.assertEqual(len(kept['pending_revision']['paragraphs']), 2)
        self.assertTrue(kept['locked'])
        self.assertEqual(next(c for c in updated['book']['chapters'] if c['id'] == childhood['id']), childhood)
        self.assertEqual(sum(c.args[0] == biography.WRITER_SYSTEM for c in calls.call_args_list), 1)
        adopted = self.review(action='adopt_chapter', chapter_id=factory['id']).json()['book']
        self.assertEqual(len(next(c for c in adopted['chapters'] if c['id'] == factory['id'])['paragraphs']), 2)
        self.assertTrue(next(c for c in adopted['chapters'] if c['id'] == factory['id'])['locked'])

    def test_manual_edits_protected_and_revision_conflicts_rejected(self):
        self.talk('小时候我和母亲住在村里。', 'childhood-01')
        first, _ = self.write()
        edited = self.review(action='edit', chapter_index=0, paragraph_index=0, text='这是我自己改过的文字。').json()['book']
        self.assertEqual(self.client.post(self.base + '/conversation-book/review', json={'revision': first['book']['revision'], 'action': 'review'}).status_code, 409)
        self.prefs(action='settings', voice_note='少用抒情和形容词。')
        updated, _ = self.write()
        self.assertEqual(updated['book']['chapters'][0]['paragraphs'][0]['text'], edited['chapters'][0]['paragraphs'][0]['text'])
        self.assertIn('pending_revision', updated['book']['chapters'][0])
        self.assertIn('pending_draft', updated['book'])

    def test_gap_selection_skip_restore_and_interview_context(self):
        self.talk('小时候我和母亲住在村里。', 'childhood-01')
        value, _ = self.write(); gap = value['gaps'][0]
        self.assertEqual(self.prefs(action='focus_gap', gap_id=gap['id']).status_code, 200)
        with store.get() as db:
            context = build(db, self.project['id'], self.session['id'], self.client.get(self.url).json()['turns'][-1]['id'])
        self.assertEqual(context['biography']['gaps'][0]['id'], gap['id'])
        self.assertTrue(context['biography']['gaps'][0]['focused'])
        self.assertEqual(self.prefs(action='skip_gap', gap_id=gap['id']).status_code, 200)
        with store.get() as db: context = biography.interview_context(db, self.project['id'])
        self.assertEqual(context['gaps'], [])
        self.assertIn(gap['question'], context['skipped_questions'])
        self.prefs(action='focus_gap', gap_id=gap['id'])
        self.assertEqual(self.client.get(self.base + '/conversation-book').json()['gaps'][0]['status'], 'open')
        self.prefs(action='resolve_gap', gap_id=gap['id'])
        with store.get() as db: self.assertEqual(biography.interview_context(db, self.project['id'])['gaps'], [])

    def test_feedback_is_not_a_source_and_stale_style_cannot_be_approved(self):
        self.talk('十六岁我进了工厂，师傅姓李。', 'factory-01')
        first, _ = self.write(); cid = first['book']['chapters'][0]['id']
        self.prefs(action='feedback', chapter_id=cid, kind='fact', note='年份需要重新核对，不能写成确定年份。')
        self.assertEqual(self.prefs(action='approve_style').status_code, 409)
        seen = []
        def model(system, value):
            if system == biography.WRITER_SYSTEM: seen.append(value)
            return self.model(system, value)
        value, _ = self.write(model=model)
        self.assertEqual(seen[0]['human_turns'][0]['text'], '十六岁我进了工厂，师傅姓李。')
        self.assertEqual(seen[0]['feedback'][0]['kind'], 'fact')
        self.assertTrue(any(g.get('chapter_id') == cid for g in value['gaps']))
        self.assertEqual(self.prefs(action='approve_style').status_code, 200)

    def test_polish_checks_continuity_and_keeps_editorial_issues(self):
        self.talk('小时候我和母亲住在村里。', 'childhood-01')
        first, _ = self.write()
        self.assertEqual(self.client.post(self.base + '/conversation-book', json={'mode': 'polish'}).status_code, 409)
        self.prefs(action='approve_style')
        def model(system, value):
            if system == biography.CONTINUITY_SYSTEM:
                return {'issues': [{'chapter_id': value['chapters'][0]['chapter_id'], 'message': '这一段的称呼需要统一。'}]}
            return self.model(system, value)
        value, _ = self.write('polish', model)
        self.assertEqual(value['book']['stage'], 'growing')
        self.assertEqual(value['book']['editorial_notes'][0]['message'], '这一段的称呼需要统一。')
        self.prefs(action='settings', voice_note='请统一人物称呼，保持自然衔接。')
        value, _ = self.write('polish')
        self.assertEqual(value['book']['stage'], 'polished')

    def test_polish_does_not_replace_manual_edits_with_same_source_digest(self):
        self.talk('小时候我和母亲住在村里。', 'childhood-01')
        self.write(); self.prefs(action='approve_style')
        self.review(action='edit', chapter_index=0, paragraph_index=0, text='本人亲自修改并希望保留的文字。')
        value, _ = self.write('polish')
        self.assertEqual(value['book']['chapters'][0]['paragraphs'][0]['text'], '本人亲自修改并希望保留的文字。')
        self.assertIn('pending_revision', value['book']['chapters'][0])
        self.assertEqual(value['book']['stage'], 'reviewing')

    def test_shared_scene_and_new_earlier_chapter_preserve_existing_ids(self):
        self.talk('十六岁我进了工厂。', 'factory-01')
        def model(system, value):
            if system == chapters.PLAN_SYSTEM:
                scene = value['new_scenes'][0]
                if not value['existing_chapters']:
                    return {'chapters': [{'title': name, 'scene_ids': [scene['id']]} for name in ['走进工厂', '师徒之间']]}
                return {'chapters': [{'title': '小时候的家', 'scene_ids': [scene['id']], 'before_chapter_id': value['existing_chapters'][0]['id']}]}
            return self.model(system, value)
        first, _ = self.write(model=model)
        self.assertEqual(len(first['book']['outline']), 2)
        self.assertEqual(first['book']['outline'][0]['scene_ids'], first['book']['outline'][1]['scene_ids'])
        self.prefs(action='approve_style'); self.write(model=model)
        self.talk('小时候我住在村里。', 'childhood-01')
        value, _ = self.write(model=model)
        self.assertEqual(value['book']['outline'][0]['title'], '小时候的家')
        self.assertEqual([c['id'] for c in value['book']['outline'][1:]], [c['id'] for c in first['book']['outline']])

    def test_long_source_tail_reaches_writer_and_cached_scene_hash_is_complete(self):
        raw = '童年在村里走过许多地方。' * 600 + '最后记得母亲在门口等我。'
        self.talk(raw, 'long-childhood-01')
        seen = []
        def model(system, value):
            if system == biography.WRITER_SYSTEM:
                seen.extend(t['text'] for t in value['human_turns'])
                t = value['human_turns'][-1]
                return {'paragraphs': [{'text': t['text'][-200:], 'evidence': [{'turn_id': t['turn_id'], 'quote': t['text'][-200:]}]}]}
            return self.model(system, value)
        value, _ = self.write(model=model)
        self.assertEqual(value['job']['status'], 'ready')
        self.assertEqual(''.join(seen), raw)
        self.assertEqual(value['book']['scenes'][0]['source_hashes'][self.human()['id']], biography.digest(raw))
        value, calls = self.write(model=lambda *_: self.fail('未变化的完整原话应复用场景和正文'))
        self.assertEqual(calls.call_count, 0)

    def test_legacy_edited_book_is_kept_until_explicit_adoption(self):
        self.talk('小时候我和母亲住在村里。', 'childhood-01')
        value, _ = self.write()
        old = {'status': 'draft', 'chapters': [{'id': 'legacy-chapter', 'title': '旧章节', 'paragraphs': [{'text': '旧版人工修改的文字。', 'edited': True, 'evidence': value['book']['chapters'][0]['paragraphs'][0]['evidence']}]}]}
        with store.get() as db: db.execute('UPDATE memoir_books SET document=? WHERE project_id=?', (json.dumps(old, ensure_ascii=False), self.project['id']))
        upgraded, _ = self.write()
        self.assertEqual(upgraded['book']['chapters'][0]['id'], 'legacy-chapter')
        self.assertEqual(upgraded['book']['chapters'][0]['paragraphs'][0]['text'], '旧版人工修改的文字。')
        self.assertIn('pending_draft', upgraded['book'])
        self.assertEqual(self.review(action='adopt').status_code, 200)

    def test_source_change_during_generation_cannot_replace_existing_book(self):
        self.talk('小时候我和母亲住在村里。', 'childhood-01')
        first, _ = self.write()
        self.prefs(action='settings', voice_note='保持口语。')
        def model(system, value):
            if system == chapters.AUDIT_SYSTEM and 'paragraphs' in value:
                with store.get() as db: db.execute("UPDATE conversation_turns SET text='本人新的更正' WHERE role='human'")
            return self.model(system, value)
        value, _ = self.write(model=model)
        self.assertEqual(value['job']['status'], 'failed')
        self.assertEqual(value['book']['revision'], first['book']['revision'])
        self.assertEqual(value['book']['chapters'], first['book']['chapters'])

    def test_scene_invented_quote_rejected_without_overwrite(self):
        self.talk('小时候我和母亲住在村里。', 'childhood-01')
        self.write(); self.talk('后来我进了工厂。', 'factory-01')
        def invented(system, value):
            if system == chapters.SCENE_SYSTEM:
                return {'scenes': [{'title': '虚构', 'summary': '虚构的雨天', 'evidence': [{'turn_id': value['human_turns'][0]['turn_id'], 'quote': '那天下着雨'}]}]}
            return self.model(system, value)
        value, _ = self.write(model=invented)
        self.assertEqual(value['job']['status'], 'failed')
        self.assertEqual(value['book']['revision'], 1)

    def test_settings_scope_revision_and_delete_cleanup(self):
        other = self.client.post('/api/platform/projects', json={'prompt': '另一人的传记', 'mode': 'demo', 'request_id': 'other-biography'}).json()
        self.prefs(action='settings', title='我的书', voice_note='朴素。')
        self.assertEqual(self.client.post(self.base + '/conversation-book/settings', json={'revision': 0, 'action': 'settings'}).status_code, 409)
        self.assertEqual(self.client.get('/api/platform/projects/' + other['id'] + '/conversation-book').json()['settings']['title'], '')
        self.client.delete(self.base)
        self.assertEqual(self.client.get(self.base + '/conversation-book').status_code, 404)
        with store.get() as db: self.assertEqual(db.execute('SELECT COUNT(*) FROM memoir_book_settings WHERE project_id=?', (self.project['id'],)).fetchone()[0], 0)


if __name__ == '__main__': unittest.main()
