"""Identity decisions retain a recoverable approval intent and portable vetoes."""
import json
from pathlib import Path

import pytest

from piia_engram import identity_review as IR, review_cli as R, tombstones
from piia_engram.core import Engram
from piia_engram.staging_review import batch_review_staging


@pytest.fixture
def eng(tmp_path):
    store = Engram(root=tmp_path / 'store')
    store.update_profile({'role': 'old'})
    return store


def mark(row, action='approve', **extra):
    return {'id': row['id'], 'mark': action, 'expected_version': row['version'], **extra}


def apply(eng, marks):
    return R.apply_marks(eng, marks, {'operator': 'owner', 'isatty': True})


@pytest.mark.parametrize('failure', ['intent', 'identity_before', 'identity_after', 'decision'])
def test_approval_faults_leave_durable_intent_and_retry(eng, monkeypatch, failure):
    row = eng.propose_identity('profile', {'role': 'new'})
    save, update = IR._save, eng.update_profile
    saves = 0

    def failing_save(store, rows):
        nonlocal saves
        saves += 1
        if (failure == 'intent' and saves == 1) or (failure == 'decision' and saves == 2):
            raise OSError('injected queue failure')
        return save(store, rows)

    def failing_update(*args, **kwargs):
        if failure == 'identity_before':
            raise OSError('injected identity failure')
        update(*args, **kwargs)
        if failure == 'identity_after':
            raise OSError('injected post-write failure')

    with monkeypatch.context() as patch:
        patch.setattr(IR, '_save', failing_save)
        patch.setattr(eng, 'update_profile', failing_update)
        with pytest.raises(OSError):
            eng.review_identity_proposal(row['id'], 'approve', expected_version=row['version'])
    current = eng.get_identity_proposals(include_decided=True)[0]
    assert current['status'] == ('pending' if failure == 'intent' else 'applying')
    assert current['before'] == {'role': 'old'} and current['after'] == {'role': 'new'}
    assert eng.get_profile()['role'] == ('new' if failure in {'identity_after', 'decision'} else 'old')
    # A fresh instance must recover solely from durable state.
    reopened = Engram(root=eng.root)
    result = reopened.review_identity_proposal(row['id'], 'approve', expected_version=row['version'])
    assert result['status'] == 'applied'
    assert reopened.get_profile()['role'] == 'new'
    decided = reopened.get_identity_proposals(include_decided=True)[0]
    assert decided['status'] == 'approved' and 'after' not in decided


def interrupt_after_identity(eng, monkeypatch):
    row = eng.propose_identity('profile', {'role': 'new'})
    save = IR._save

    def fail_terminal(store, rows):
        if any(r['status'] == 'approved' for r in rows):
            raise OSError('injected terminal failure')
        return save(store, rows)

    with monkeypatch.context() as patch:
        patch.setattr(IR, '_save', fail_terminal)
        with pytest.raises(OSError):
            eng.review_identity_proposal(row['id'], 'approve')
    return row


def test_reject_cannot_contradict_interrupted_approval(eng, monkeypatch):
    row = interrupt_after_identity(eng, monkeypatch)
    assert eng.review_identity_proposal(row['id'], 'reject')['status'] == 'approval_in_progress'
    assert not tombstones.by_id(eng.root, row['id'])
    assert eng.get_identity_proposals()[0]['status'] == 'applying'
    assert eng.review_identity_proposal(row['id'], 'approve')['status'] == 'applied'
    assert eng.review_identity_proposal(row['id'], 'reject')['status'] == 'already_decided'


@pytest.mark.parametrize('stage', ['applying', 'approved'])
def test_queue_write_ack_failure_is_replayable(eng, monkeypatch, stage):
    row = eng.propose_identity('profile', {'role': 'new'})
    save = IR._save

    def fail_after_save(store, rows):
        save(store, rows)
        if rows[0]['status'] == stage:
            raise OSError('injected post-save failure')

    with monkeypatch.context() as patch:
        patch.setattr(IR, '_save', fail_after_save)
        with pytest.raises(OSError):
            eng.review_identity_proposal(row['id'], 'approve')
    assert eng.get_identity_proposals(include_decided=True)[0]['status'] == stage
    assert eng.get_profile()['role'] == ('old' if stage == 'applying' else 'new')
    assert eng.review_identity_proposal(row['id'], 'approve')['status'] == (
        'applied' if stage == 'applying' else 'already_applied')
    assert eng.get_profile()['role'] == 'new'


@pytest.mark.parametrize('fix', [False, True])
def test_doctor_reports_and_finishes_interrupted_approval(eng, monkeypatch, fix):
    from piia_engram.doctor import _run_identity_recovery_check
    row = interrupt_after_identity(eng, monkeypatch)
    assert _run_identity_recovery_check(eng, fix=fix) == (0 if fix else 1)
    assert eng.get_identity_proposals(include_decided=True)[0]['status'] == ('approved' if fix else 'applying')
    assert eng.get_profile()['role'] == 'new'


def test_recovery_preserves_later_local_edit(eng, monkeypatch):
    row = interrupt_after_identity(eng, monkeypatch)
    eng.update_profile({'role': 'local'})
    result = eng.recover_identity_proposals()
    assert result[0]['status'] == 'identity_conflict'
    assert eng.get_profile()['role'] == 'local'
    assert eng.review_identity_proposal(row['id'], 'reject')['status'] == 'approval_in_progress'


@pytest.mark.parametrize('surface', ['marks', 'batch'])
def test_identity_batch_preview_simulates_same_field_order(eng, surface):
    rows = [eng.propose_identity('profile', {'role': value}) for value in ['A', 'B']]
    if surface == 'marks':
        marks = [mark(r) for r in rows]
        preview = R.preview_marks(eng, marks)
        applied = apply(eng, marks)
    else:
        actions = [{'id': r['id'], 'action': 'approve'} for r in rows]
        preview = batch_review_staging(eng, actions)
        applied = batch_review_staging(eng, actions, dry_run=False, confirm=True, owner_cli=True)
    assert [i['status'] for i in preview['items']] == ['planned', 'identity_conflict']
    assert [i['status'] for i in applied['items']] == ['applied', 'identity_conflict']
    assert eng.get_profile()['role'] == 'A'


@pytest.mark.parametrize('action,extra', [('edit-type', {'type': 'rule'}),
                                        ('supersede', {'target': 'other'}),
                                        ('retire', {}), ('restore', {})])
def test_unsupported_identity_marks_match_preview_and_apply(eng, action, extra):
    row = eng.propose_identity('profile', {'role': 'new'})
    marks = [mark(row, action, **extra)]
    assert R.preview_marks(eng, marks)['items'][0]['status'] == 'invalid_action'
    assert apply(eng, marks)['items'][0]['status'] == 'invalid_action'
    assert eng.get_profile()['role'] == 'old'
    assert eng.get_identity_proposals()[0]['status'] == 'pending'


@pytest.mark.parametrize('merge', [True, False])
def test_identity_rejection_survives_backup_restore(eng, tmp_path, merge):
    desired = 'rejected private identity text'
    row = eng.propose_identity('profile', {'role': desired})
    eng.review_identity_proposal(row['id'], 'reject')
    output = eng.export_all(str(tmp_path / 'backup.json'))
    backup = json.loads(Path(output).read_text(encoding='utf-8'))
    stones = backup['knowledge']['tombstones']
    assert len(stones) == 1 and stones[0]['kind'] == 'identity'
    assert stones[0]['hv'] == 4
    assert desired not in json.dumps(backup)
    dst = Engram(root=tmp_path / 'restored')
    dst.import_all(output, merge=merge)
    dst.import_all(output, merge=True)
    assert len(tombstones.load(dst.root)) == 1
    assert dst.propose_identity('profile', {'role': desired})['status'] == 'rejected_before'


def test_identical_identity_change_refused_only_against_same_base(eng):
    row = eng.propose_identity('profile', {'role': 'desired'})
    eng.update_profile({'role': 'local'})
    assert eng.review_identity_proposal(row['id'], 'approve')['status'] == 'identity_conflict'
    eng.review_identity_proposal(row['id'], 'reject')
    fresh = eng.propose_identity('profile', {'role': 'desired'})
    assert fresh['status'] == 'pending'
    eng.review_identity_proposal(fresh['id'], 'reject')
    assert eng.propose_identity('profile', {'role': 'desired'})['status'] == 'rejected_before'
    eng.update_profile({'role': 'old'})
    assert eng.propose_identity('profile', {'role': 'desired'})['status'] == 'rejected_before'


def test_legacy_identity_veto_roundtrip_requires_explicit_withdrawal(eng, tmp_path):
    row = eng.propose_identity('profile', {'role': 'desired'})
    h1, h2 = tombstones.claim_hashes_for_version('identity', row, 3)
    stone = {'id': 'legacy-veto', 'kind': 'identity', 'hv': 3,
             'scope': 'global', 'h1': h1, 'h2': h2}
    path = eng.root / 'knowledge' / tombstones.FILENAME
    path.write_text(json.dumps(stone) + '\n', encoding='utf-8')
    output = eng.export_all(str(tmp_path / 'backup.json'))
    dst = Engram(root=tmp_path / 'restored')
    dst.import_all(output)
    dst.update_profile({'role': 'different-base'})
    assert dst.propose_identity('profile', {'role': 'desired'})['status'] == 'rejected_before'
    tombstones.remove(dst.root, 'legacy-veto')
    assert dst.propose_identity('profile', {'role': 'desired'})['status'] == 'pending'


def test_missing_and_null_base_values_have_distinct_fingerprints(eng):
    eng.update_preferences({})
    first = eng.propose_identity('preferences', {'communication': 'desired'})
    eng.review_identity_proposal(first['id'], 'reject')
    eng.update_preferences({'communication': None})
    assert eng.propose_identity('preferences', {'communication': 'desired'})['status'] == 'pending'


def test_identity_preview_tracks_legacy_preference_fallback(eng):
    eng.update_work_style({'communication': 'old'})
    first = eng.propose_identity('work_style', {'communication': 'new'})
    second = eng.propose_identity('preferences', {'communication': 'different'})
    marks = [mark(first), mark(second)]
    assert [r['status'] for r in R.preview_marks(eng, marks)['items']] == ['planned', 'identity_conflict']
    assert [r['status'] for r in apply(eng, marks)['items']] == ['applied', 'identity_conflict']


@pytest.mark.parametrize('field,updates', [
    ('profile', {'role': 'new'}), ('preferences', {'communication': 'new'}),
    ('work_style', {'communication': 'new'}), ('quality_standards', {'rules': ['new']}),
    ('trust_boundaries', {'restricted_fields': ['role']}),
])
def test_all_identity_fields_resume_after_identity_write(eng, monkeypatch, field, updates):
    row = eng.propose_identity(field, updates)
    save = IR._save

    def fail_terminal(store, rows):
        if rows[-1]['status'] == 'approved':
            raise OSError('injected terminal failure')
        return save(store, rows)

    with monkeypatch.context() as patch:
        patch.setattr(IR, '_save', fail_terminal)
        with pytest.raises(OSError):
            eng.review_identity_proposal(row['id'], 'approve')
    assert eng.get_identity_proposals()[0]['status'] == 'applying'
    assert eng.recover_identity_proposals()[0]['status'] == 'applied'
    current = getattr(eng, 'get_' + field)()
    assert all(current[key] == value for key, value in updates.items())


def test_recovery_never_writes_from_mcp_or_read_only_handle(eng, monkeypatch):
    from piia_engram import write_provenance
    row = interrupt_after_identity(eng, monkeypatch)
    before = (eng.root / 'identity' / 'proposals.json').read_bytes()
    with write_provenance.origin_scope('mcp'):
        assert eng.recover_identity_proposals()[0]['status'] == 'local_review_only'
    readonly = Engram(root=eng.root, read_only=True)
    assert readonly.recover_identity_proposals()['error'] == 'read_only'
    assert (eng.root / 'identity' / 'proposals.json').read_bytes() == before
