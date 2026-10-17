"""Fail-closed, additive-only test policy. Model output is data, never Java."""
import base64
import hashlib
import json
from pathlib import Path


def sha(data):return hashlib.sha256(data).hexdigest()


def inventory(workspace):
    root=Path(workspace).resolve(strict=True);result={}
    for p in (root/'url-shortener').rglob('*'):
        rel=p.relative_to(root)
        if len(rel.parts)>1 and rel.parts[1] in ('target','.git','.jdk-25'):continue
        if p.is_symlink():raise PermissionError(f'Symlink in protected project: {rel}')
        if p.is_file():result[str(rel)]=p.read_bytes()
    return result


def preserve(workspace,directory):
    """Store independent byte snapshots outside the editable workspace."""
    root=Path(workspace).resolve();directory=Path(directory).resolve()
    if directory.is_relative_to(root):raise PermissionError('Test baseline must be outside workspace')
    directory.mkdir(parents=True,exist_ok=False)
    files=inventory(root)
    if not any(p.startswith('url-shortener/src/test/java/') for p in files):raise PermissionError('No original tests to preserve')
    manifest={p:{'sha256':sha(data),'content':base64.b64encode(data).decode()} for p,data in files.items()}
    raw=json.dumps(manifest,sort_keys=True).encode();(directory/'baseline.json').write_bytes(raw)
    return sha(raw)


def load_baseline(directory,expected_hash):
    raw=(Path(directory)/'baseline.json').read_bytes()
    if sha(raw)!=expected_hash:raise PermissionError('Independent baseline integrity failure')
    manifest=json.loads(raw);files={}
    for p,record in manifest.items():
        data=base64.b64decode(record['content'],validate=True)
        if sha(data)!=record['sha256']:raise PermissionError('Baseline file hash mismatch')
        files[p]=data
    return files


def assert_integrity(workspace,baseline,approved_changes=None):
    expected={**baseline,**(approved_changes or {})}
    current=inventory(workspace)
    if current.keys()!=expected.keys() or any(current[p]!=data for p,data in expected.items()):
        raise PermissionError('Unauthorized project/test/build change; preservation cannot be verified')


def new_test_target(workspace,path):
    rel=Path(path);root=Path(workspace).resolve(strict=True)
    if (not isinstance(path,str) or rel.is_absolute() or '..' in rel.parts or rel.as_posix()!=path
        or not rel.is_relative_to('url-shortener/src/test/java') or rel.suffix!='.java'):
        raise PermissionError('Only explicitly authorized new Java test paths are supported')
    target=root/rel
    if target.exists() or target.is_symlink():raise PermissionError('Cannot overwrite an existing test')
    if target.parent.resolve(strict=True)!=target.parent:raise PermissionError('Test path cannot use symlink aliases')
    return target
