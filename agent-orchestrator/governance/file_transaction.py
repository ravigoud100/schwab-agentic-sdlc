"""Staged Java edits with durable recovery records and compensating rollback.

Each replacement is atomic. A workspace lock serializes cooperating executors.
The journal detects interrupted transactions; this is not an OS-level multi-file
transaction and cannot prevent an unrelated process from writing source files.
"""
import base64
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import tempfile
import uuid

from governance.audit_logger import log_event
from governance.rollback_manager import create_checkpoint, file_hash
from governance.source_policy import authorized_java_target, authorized_new_java_target


class RecoveryRequired(RuntimeError):
    pass


@contextmanager
def workspace_lock(workspace):
    directory = workspace.resolve(strict=True) / '.agent-transactions'
    directory.mkdir(exist_ok=True)
    if directory.is_symlink():
        raise PermissionError('Transaction directory cannot be a symlink')
    if (directory / 'lock').is_symlink():
        raise PermissionError('Transaction lock cannot be a symlink')
    with (directory / 'lock').open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield directory
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def sync_directory(directory):
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def save_journal(path, record):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as stream:
        json.dump(record, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    sync_directory(path.parent)


def stage_file(target, content):
    descriptor, name = tempfile.mkstemp(prefix='.agent-edit-', dir=target.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        # New, prospectively approved sources have no target mode to preserve.
        os.chmod(name, target.stat().st_mode & 0o777 if target.exists() else 0o644)
        return Path(name)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise


def _target(entry, workspace, allowed_files, allowed_new_files):
    if entry.get('created'):
        # A journaled creation now exists; validate its canonical location without
        # using the creation helper, which correctly rejects existing paths.
        if entry['path'] not in allowed_new_files:
            raise PermissionError('New file is not in the approved task allowlist')
        root = Path(workspace).resolve(strict=True)
        target = root / entry['path']
        if target.is_symlink() or target.parent.resolve(strict=True) != root / target.parent.relative_to(root):
            raise PermissionError('New source path cannot use symlink aliases')
        return target
    return authorized_java_target(entry['path'], workspace, allowed_files)


def _restore(record, workspace, allowed_files, allowed_new_files=()):
    """Preflight the entire set before restoring any file."""
    pending = []
    for entry in record['files']:
        if entry.get('created'):
            # Creation rollback must never delete a path claimed by another writer.
            path = Path(workspace).resolve(strict=True) / entry['path']
            if not path.exists():
                continue
            if path.is_symlink() or file_hash(path.read_bytes()) != entry['expected_current_hash']:
                raise RecoveryRequired(f"Source changed externally: {entry['path']}")
            pending.append((path, None, entry['expected_current_hash']))
            continue
        target = _target(entry, workspace, allowed_files, allowed_new_files)
        original = base64.b64decode(entry['original_content'], validate=True)
        if file_hash(original) != entry['original_hash']:
            raise ValueError('Checkpoint integrity verification failed')
        current_hash = file_hash(target.read_bytes())
        if current_hash == entry['original_hash']:
            continue
        if current_hash != entry['expected_current_hash']:
            raise RecoveryRequired(f"Source changed externally: {entry['path']}")
        pending.append((target, original, entry['expected_current_hash']))
    staged = []
    try:
        for target, original, expected in pending:
            staged.append((target, None if original is None else stage_file(target, original), expected))
        # Recheck every file immediately before beginning compensation.
        for target, _, expected in staged:
            if file_hash(target.read_bytes()) != expected:
                raise RecoveryRequired('Source changed before rollback')
        for target, temporary, expected in staged:
            if file_hash(target.read_bytes()) != expected:
                raise RecoveryRequired('Source changed during rollback')
            if temporary is None:
                target.unlink()
            else:
                os.replace(temporary, target)
            sync_directory(target.parent)
    finally:
        for _, temporary, _ in staged:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def ensure_workspace_ready(workspace):
    if not (workspace.resolve(strict=True) / '.agent-transactions').exists():
        return
    with workspace_lock(workspace) as directory:
        for journal in directory.glob('*.json'):
            if json.loads(journal.read_text())['status'] not in {'validated', 'rolled_back'}:
                raise RecoveryRequired(f'Unfinished transaction requires recovery: {journal.name}')


def apply_transaction(proposals, originals, workspace, allowed_files, workflow_id, *, policy_approvals=(), allowed_new_files=()):
    """All proposals and original hashes must be validated before the first write."""
    with workspace_lock(workspace) as directory:
        for journal in directory.glob('*.json'):
            if json.loads(journal.read_text())['status'] not in {'validated', 'rolled_back'}:
                raise RecoveryRequired(f'Unfinished transaction requires recovery: {journal.name}')
        from governance.policy_engine import enforce
        enforce(proposals, originals, workspace, workflow_id, approvals=policy_approvals,
                allow_new_source_files=allowed_new_files)
        targets = {}
        checkpoints = []
        for relative, content in proposals.items():
            created = originals[relative] is None
            target = (authorized_new_java_target(relative, workspace, allowed_new_files)
                      if created else authorized_java_target(relative, workspace, allowed_files))
            if target in targets.values():
                raise PermissionError('Multiple paths alias the same target')
            targets[relative] = target
            if created:
                checkpoint = {'path': relative, 'original_content': None, 'original_hash': None, 'created': True}
            else:
                checkpoint = create_checkpoint(relative, workspace, allowed_files=allowed_files)
                if checkpoint['original_content'] != originals[relative]:
                    raise RuntimeError('Source changed during generation; refusing implementation')
            checkpoints.append(checkpoint)
        identifier = str(uuid.uuid4())
        journal = directory / (identifier + '.json')
        record = {'transaction_id': identifier, 'workflow_id': workflow_id, 'status': 'prepared', 'files': [
            {'path': cp['path'], 'original_content': base64.b64encode(cp['original_content']).decode() if cp['original_content'] is not None else None,
             'original_hash': cp['original_hash'], 'created': bool(cp.get('created')), 'expected_current_hash': file_hash(proposals[cp['path']]),
             'expected_content': base64.b64encode(proposals[cp['path']]).decode()}
            for cp in checkpoints
        ]}
        staged = []
        save_journal(journal, record)
        try:
            for entry in record['files']:
                log_event(workflow_id, 'implementation_executor', 'FILE_CHECKPOINT_CREATED', {
                    'path': entry['path'], 'checkpoint_id': identifier + ':' + entry['path'],
                    'transaction_id': identifier, 'original_hash': entry['original_hash']})
            for relative, target in targets.items():
                staged.append((relative, target, stage_file(target, proposals[relative])))
            for relative, target, _ in staged:
                if ((originals[relative] is None and target.exists()) or
                    (originals[relative] is not None and (authorized_java_target(relative, workspace, allowed_files) != target or target.read_bytes() != originals[relative]))):
                    raise RuntimeError('Source changed before applying transaction')
            record['status'] = 'applying'
            save_journal(journal, record)
            for relative, target, temporary in staged:
                if ((originals[relative] is None and target.exists()) or
                    (originals[relative] is not None and (authorized_java_target(relative, workspace, allowed_files) != target or target.read_bytes() != originals[relative]))):
                    raise RuntimeError('Source changed while applying transaction')
                os.replace(temporary, target)
                sync_directory(target.parent)
            record['status'] = 'applied'
            save_journal(journal, record)
            log_event(workflow_id, 'implementation_executor', 'TRANSACTION_APPLIED', {
                'transaction_id': identifier, 'paths': list(proposals)})
        except BaseException as error:
            try:
                _restore(record, workspace, allowed_files, allowed_new_files)
                record['status'] = 'rolled_back'
                save_journal(journal, record)
                log_event(workflow_id, 'implementation_executor', 'TRANSACTION_APPLY_ROLLED_BACK',
                          {'transaction_id': identifier, 'paths': list(proposals)})
            except BaseException as recovery_error:
                record['status'] = 'recovery_required'
                save_journal(journal, record)
                raise RecoveryRequired(f'Transaction {identifier} requires recovery') from recovery_error
            raise error
        finally:
            for _, _, temporary in staged:
                temporary.unlink(missing_ok=True)
        return {'transaction_id': identifier, 'paths': list(proposals), 'checkpoints': checkpoints,
                'expected_hashes': {entry['path']: entry['expected_current_hash'] for entry in record['files']}}


def finish_transaction(transaction_id, workspace, allowed_files, *, passed, allowed_new_files=()):
    # The transaction ID comes from executor state, not the model.
    if str(uuid.UUID(transaction_id)) != transaction_id:
        raise ValueError('Invalid transaction ID')
    with workspace_lock(workspace) as directory:
        journal = directory / (transaction_id + '.json')
        record = json.loads(journal.read_text())
        if record['status'] == 'rolled_back' and not passed:
            return
        if passed:
            if record['status'] != 'applied':
                raise RecoveryRequired('Only fully applied transactions can be validated')
            for entry in record['files']:
                target = _target(entry, workspace, allowed_files, allowed_new_files)
                if file_hash(target.read_bytes()) != entry['expected_current_hash']:
                    raise RecoveryRequired('Source changed during Maven validation')
            record['status'] = 'validated'
        else:
            try:
                _restore(record, workspace, allowed_files, allowed_new_files)
                record['status'] = 'rolled_back'
                log_event(record['workflow_id'], 'rollback_manager', 'TRANSACTION_ROLLED_BACK', {
                    'transaction_id': transaction_id, 'paths': [entry['path'] for entry in record['files']]})
            except BaseException:
                record['status'] = 'recovery_required'
                save_journal(journal, record)
                raise
        save_journal(journal, record)
