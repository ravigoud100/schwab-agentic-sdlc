"""Durable, hash-protected publication and compensation for one additive test."""
import json, os, tempfile, uuid
from pathlib import Path
from governance.file_transaction import save_journal, sync_directory, workspace_lock
from governance.rollback_manager import file_hash
from governance.test_safety import new_test_target
from governance.policy_engine import enforce


def create_test(path, content, workspace, workflow_id, allowed):
    workspace=Path(workspace)
    with workspace_lock(workspace) as directory:
        if path not in allowed:raise PermissionError('Unauthorized additive test')
        enforce({path:content},{path:None},workspace,workflow_id,allow_new_test_files=allowed)
        target=new_test_target(workspace,path)
        identifier=str(uuid.uuid4()); journal=directory/(identifier+'.additive-test.json')
        record={'transaction_id':identifier,'workflow_id':workflow_id,'status':'prepared','path':path,
                'created_hash':file_hash(content)};save_journal(journal,record)
        fd,name=tempfile.mkstemp(prefix='.agent-test-',dir=target.parent);temporary=Path(name)
        try:
            with os.fdopen(fd,'wb') as stream:stream.write(content);stream.flush();os.fsync(stream.fileno())
            if target.exists():raise RuntimeError('Test path appeared before publication')
            os.replace(temporary,target);sync_directory(target.parent)
            record['status']='applied';save_journal(journal,record)
        finally:temporary.unlink(missing_ok=True)
        return record


def rollback_test(record, workspace):
    workspace=Path(workspace)
    with workspace_lock(workspace) as directory:
        journal=directory/(record['transaction_id']+'.additive-test.json')
        stored=json.loads(journal.read_text())
        if stored!=record:raise PermissionError('Additive test journal changed')
        target=workspace/stored['path']
        if target.exists():
            if target.is_symlink() or file_hash(target.read_bytes())!=stored['created_hash']:
                raise PermissionError('Generated test changed externally; rollback blocked')
            target.unlink();sync_directory(target.parent)
        stored['status']='rolled_back';save_journal(journal,stored)
        return stored


def finish_test(record, workspace):
    workspace=Path(workspace)
    with workspace_lock(workspace) as directory:
        journal=directory/(record['transaction_id']+'.additive-test.json');stored=json.loads(journal.read_text())
        target=workspace/stored['path']
        if not target.is_file() or file_hash(target.read_bytes())!=stored['created_hash']:
            raise PermissionError('Generated test changed before validation completion')
        stored['status']='validated';save_journal(journal,stored);return stored
