"""Offline DB + uploads snapshots. Stop every writer before using --server-stopped."""
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


def fingerprint(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return {'size': path.stat().st_size, 'sha256': digest.hexdigest()}


def files(root):
    if root.is_symlink() or root.is_junction():
        raise ValueError('Links/junctions are forbidden in snapshot data')
    result = {}
    for path in root.rglob('*'):
        if path.is_symlink() or path.is_junction():
            raise ValueError('Links/junctions are forbidden in snapshot data')
        if path.is_file():
            result[path.relative_to(root).as_posix()] = fingerprint(path)
    return result


def check_database(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Database integrity check failed')
        if db.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('Database contains broken foreign keys')
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table, folder in [('attachments', 'attachments'), ('exam_reports', 'exams')]:
            if table not in tables:
                continue
            for (stored_name,) in db.execute('SELECT stored_name FROM ' + table):
                if not stored_name or Path(stored_name).name != stored_name or ':' in stored_name or '\\' in stored_name:
                    raise ValueError('Unsafe registered attachment name')
                if not (path.parent / 'uploads' / folder / stored_name).is_file():
                    raise ValueError('Registered attachment is missing: ' + stored_name)


def backup(source, destination, *, server_stopped=False):
    if not server_stopped:
        raise ValueError('Stop all writers first; pass --server-stopped explicitly')
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination or source in destination.parents:
        raise ValueError('Backup must be outside source directory')
    if not (source / 'elderly_care.db').is_file():
        raise ValueError('Source must contain elderly_care.db')
    if (source / 'elderly_care.db').is_symlink():
        raise ValueError('Linked database is forbidden')
    before = files(source / 'uploads') if (source / 'uploads').exists() else {}
    destination.mkdir(parents=True, exist_ok=False)
    try:
        with closing(sqlite3.connect((source / 'elderly_care.db').as_uri() + '?mode=ro', uri=True)) as src:
            with closing(sqlite3.connect(destination / 'elderly_care.db')) as dst:
                src.backup(dst)
        if (source / 'uploads').exists():
            shutil.copytree(source / 'uploads', destination / 'uploads')
        else:
            (destination / 'uploads').mkdir()
        after = files(source / 'uploads') if (source / 'uploads').exists() else {}
        if before != after or before != files(destination / 'uploads'):
            raise ValueError('Uploads changed while copying; stop all writers and retry')
        check_database(destination / 'elderly_care.db')
        manifest = {'version': 1, 'created_at': datetime.now(timezone.utc).isoformat(),
                    'consistency': 'offline-all-writers-stopped', 'files': files(destination)}
        (destination / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        verify(destination)
    except Exception:
        # Keep failed output for diagnosis; never mistake it for a verified snapshot.
        (destination / 'BACKUP_FAILED').write_text('Do not restore this incomplete snapshot', encoding='utf-8')
        raise
    return destination


def verify(snapshot):
    snapshot = Path(snapshot).resolve()
    manifest = json.loads((snapshot / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('version') != 1 or not isinstance(manifest.get('files'), dict):
        raise ValueError('Unsupported manifest')
    for name in manifest['files']:
        parts = PurePosixPath(name)
        if '\\' in name or ':' in name or parts.is_absolute() or '..' in parts.parts:
            raise ValueError('Unsafe manifest path')
        if name != 'elderly_care.db' and not name.startswith('uploads/'):
            raise ValueError('Unexpected snapshot entry')
    actual = files(snapshot)
    actual.pop('manifest.json', None)
    if actual != manifest['files'] or 'elderly_care.db' not in actual:
        raise ValueError('Snapshot file list, size or SHA-256 mismatch')
    check_database(snapshot / 'elderly_care.db')
    return manifest


def restore(snapshot, target, *, server_stopped=False, replace=False):
    if not server_stopped:
        raise ValueError('Stop all writers first; pass --server-stopped explicitly')
    snapshot, target = Path(snapshot).resolve(), Path(target).resolve()
    if target == snapshot or target in snapshot.parents or snapshot in target.parents:
        raise ValueError('Snapshot and restore target must be separate directories')
    verify(snapshot)  # Before creating or moving anything in target.
    if target.exists() and (not target.is_dir() or (any(target.iterdir()) and not replace)):
        raise ValueError('Nonempty target requires explicit --replace')
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=target.name + '-restore-', dir=target.parent))
    retained = None
    try:
        shutil.copy2(snapshot / 'elderly_care.db', staging / 'elderly_care.db')
        shutil.copytree(snapshot / 'uploads', staging / 'uploads')
        shutil.copy2(snapshot / 'manifest.json', staging / 'manifest.json')
        verify(staging)
        if target.exists():
            retained = target.with_name(target.name + '-before-restore-' + uuid.uuid4().hex[:12])
            os.replace(target, retained)  # Preserve the entire current directory, including unknown files.
        try:
            os.replace(staging, target)
        except Exception:
            if retained is not None:
                os.replace(retained, target)
            raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return retained


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('backup')
    p.add_argument('--source', required=True)
    p.add_argument('--destination', required=True)
    p.add_argument('--server-stopped', action='store_true')
    p = commands.add_parser('restore')
    p.add_argument('--snapshot', required=True)
    p.add_argument('--target', required=True)
    p.add_argument('--server-stopped', action='store_true')
    p.add_argument('--replace', action='store_true')
    p = commands.add_parser('verify')
    p.add_argument('--snapshot', required=True)
    args = parser.parse_args()
    if args.command == 'backup':
        print('Verified snapshot:', backup(args.source, args.destination, server_stopped=args.server_stopped))
    elif args.command == 'restore':
        print('Restored; retained previous directory:', restore(args.snapshot, args.target, server_stopped=args.server_stopped, replace=args.replace))
    else:
        print(json.dumps(verify(args.snapshot), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
