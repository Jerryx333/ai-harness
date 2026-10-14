"""Offline worktree lifecycle and recoverable archive regression cases."""
import contextlib
import datetime as dt
import fcntl
import hashlib
import io
import shutil
from unittest import mock
from support import Path, REPO, check, git_template, inprocess_sh, json, os, sh, sys, tempfile


@contextlib.contextmanager
def fixture():
    import ai_worker as w
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        repo = git_template(root / 'repo', {'tracked': 'base\n', 'staged': 'base\n', 'binary': 'base\n', '.gitignore': '__pycache__/\nignored\n'})
        with mock.patch.object(w, 'STATE', root / 'state'):
            wd, fields, source, head = w.create_task_worktree(str(repo), root / 'runs/task')
            path = Path(wd)
            meta = path.parent / (path.name + '.json')
            yield w, root, repo, path, fields, head, meta


def git(repo, *args):
    r = sh(['git', '-C', str(repo), *args])
    check(r.returncode == 0, r.stderr)
    return r.stdout.strip()


def expire(meta):
    data = json.loads(meta.read_text())
    data['created_at'] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=8)).isoformat()
    meta.write_text(json.dumps(data))


def gone(repo, path, fields, meta):
    check(not path.exists() and not meta.exists(), '目录或元数据残留')
    check(sh(['git', '-C', str(repo), 'show-ref', '--verify', '--quiet',
              'refs/heads/' + fields['worktree_branch']]).returncode == 1, '分支残留')


def worktree_empty_dispatch_cleanup():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td); repo = git_template(root / 'repo', {'tracked': 'base\n'})
        prompt = root / 'prompt'; prompt.write_text('no changes')
        state = root / 'state'
        for mode, rc in [('ok', 0), ('quota', 1)]:
            (state / 'breaker.json').unlink(missing_ok=True)
            r = inprocess_sh([sys.executable, str(REPO / 'ai_worker.py'), 'codex', str(repo), '10',
                              str(prompt), '--worktree', '--no-fallback', '--no-findings'],
                             env=dict(os.environ, AIW_STATE=str(state), FAKE_CODEX=mode))
            check(r.returncode == rc, r.stderr)
            row = json.loads((state / 'seat-ledger.jsonl').read_text().splitlines()[-1])
            gone(repo, Path(row['worktree']), row, Path(row['worktree'] + '.json'))
            check('本单无改动，worktree 已清理' in r.stderr and 'merge --ff-only' not in r.stderr,
                  r.stderr)


def worktree_dirty_metadata():
    with fixture() as (w, root, repo, path, fields, head, meta):
        data = json.loads(meta.read_text())
        check(set(data) == {'path', 'branch', 'source_root', 'base_head', 'created_at'}, str(data))
        check(data['base_head'] == head and data['path'] == str(path) and data['source_root'] == str(repo), str(data))
        check(dt.datetime.fromisoformat(data['created_at']).utcoffset() == dt.timedelta(), '不是 UTC')
        (path / 'tracked').write_text('staged\n'); git(path, 'add', 'tracked')
        (path / 'tracked').write_text('base\n')
        w.report_task_worktree(fields, repo, head)
        check(path.exists() and meta.exists(), '暂存改动被清理')


def worktree_merged_cleanup():
    with fixture() as (w, root, repo, path, fields, head, meta):
        (path / 'tracked').write_text('committed\n'); git(path, 'add', '-A'); git(path, 'commit', '-qm', 'change')
        git(repo, 'merge', '--ff-only', fields['worktree_branch'])
        (path / '__pycache__').mkdir(); (path / '__pycache__/cache.pyc').write_bytes(b'cache')
        w.sweep_worktrees()
        gone(repo, path, fields, meta)
        check(not (path.parent / 'archive').exists(), '已合并仍存档')


def worktree_recent_retained():
    with fixture() as (w, root, repo, path, fields, head, meta):
        (path / 'tracked').write_text('committed\n'); git(path, 'add', '-A'); git(path, 'commit', '-qm', 'change')
        w.sweep_worktrees()
        check(path.exists() and meta.exists(), '7 天内未合并被清理')


def worktree_expired_binary_restore():
    with fixture() as (w, root, repo, path, fields, head, meta):
        (path / 'tracked').write_text('committed\n'); git(path, 'add', '-A'); git(path, 'commit', '-qm', 'change')
        (path / 'staged').write_text('staged\n'); git(path, 'add', 'staged')
        (path / 'tracked').write_text('committed plus unstaged\n')
        (path / 'untracked').write_text('new\n')
        (path / 'binary').write_bytes(b'\x00\xff\x01binary\x00')
        (path / 'ignored').write_bytes(b'\x00ignored')
        expected = {p.name: p.read_bytes() for p in path.iterdir() if p.name not in {'.git', 'ignored'}}
        index = Path(git(path, 'rev-parse', '--absolute-git-dir')) / 'index'
        original_index = index.read_bytes()
        archive = path.parent / 'archive/task.patch'
        w.archive_task_worktree(path, head, archive)
        check(index.read_bytes() == original_index, '真实 index 被改动')
        expire(meta); w.sweep_worktrees()
        gone(repo, path, fields, meta)
        check(b'diff --git a/ignored' not in archive.read_bytes(), '补丁含被忽略文件')
        git(repo, 'apply', '--binary', str(archive))
        check(not (repo / 'ignored').exists(), '恢复了被忽略文件')
        for name, content in expected.items():
            check((repo / name).read_bytes() == content, '还原不同: ' + name)


def worktree_archive_failure_retained():
    with fixture() as (w, root, repo, path, fields, head, meta):
        (path / 'untracked').write_text('must retain'); expire(meta)
        with mock.patch.object(w, 'atomic_write_bytes', side_effect=OSError('archive failed')):
            output = io.StringIO(); w.sweep_worktrees(output=output)
        check(path.exists() and meta.exists() and (path / 'untracked').read_text() == 'must retain', '失败后删了内容')
        check('失败' in output.getvalue(), output.getvalue())


def worktree_locked_skipped():
    with fixture() as (w, root, repo, path, fields, head, meta):
        expire(meta)
        key = hashlib.sha1(str(path.resolve()).encode()).hexdigest()[:12]
        with (w.STATE / ('lock-' + key)).open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            output = io.StringIO(); w.sweep_worktrees(output=output)
            check(path.exists() and meta.exists() and '写锁正在使用' in output.getvalue(), output.getvalue())


def worktree_missing_source_cleanup():
    with fixture() as (w, root, repo, path, fields, head, meta):
        (path / 'untracked').write_text('must save')
        shutil.rmtree(repo); w.sweep_worktrees()
        check(not path.exists() and not meta.exists(), '残留没有清理')
        check((path.parent / 'archive/task.files/untracked').read_text() == 'must save', '源仓库丢失时丢改动')


def worktree_missing_directory_prune():
    with fixture() as (w, root, repo, path, fields, head, meta):
        shutil.rmtree(path); w.sweep_worktrees()
        check(not meta.exists() and str(path) not in git(repo, 'worktree', 'list'), '没有 prune')


def worktree_gc_dry_run_and_patches():
    with fixture() as (w, root, repo, path, fields, head, meta):
        expire(meta); (path / 'untracked').write_text('save')
        archive = path.parent / 'archive'; archive.mkdir()
        old = archive / 'old.patch'; old.write_text('old patch')
        fresh = archive / 'fresh.patch'; fresh.write_text('fresh patch')
        os.utime(old, (0, 0))
        old_files = archive / 'old.files'; old_files.mkdir(); (old_files / 'saved').write_text('old')
        fresh_files = archive / 'fresh.files'; fresh_files.mkdir()
        linked_files = archive / 'linked.files'; linked_files.symlink_to(old_files, target_is_directory=True)
        os.utime(old_files, (0, 0))
        orphan = path.parent / 'orphan'; orphan.mkdir(); (orphan / 'file').write_text('keep')
        env = dict(os.environ, AIW_STATE=str(w.STATE))
        before = {str(p): p.read_bytes() for p in w.STATE.rglob('*') if p.is_file()}
        r = inprocess_sh([sys.executable, str(REPO / 'gc.py'), '30', '--dry-run'], env=env)
        after = {str(p): p.read_bytes() for p in w.STATE.rglob('*') if p.is_file()}
        check(r.returncode == 0 and before == after and '没有元数据' in r.stdout, r.stdout + r.stderr)
        check(path.exists() and meta.exists() and old.exists() and old_files.exists() and 'old.files' in r.stdout, 'dry-run 删除内容')
        r = inprocess_sh([sys.executable, str(REPO / 'gc.py'), '30'], env=env)
        check(r.returncode == 0, r.stderr)
        gone(repo, path, fields, meta)
        check(not old_files.exists() and fresh_files.exists() and linked_files.is_symlink(), r.stdout)
        check(not old.exists() and fresh.exists() and (archive / 'task.patch').exists() and orphan.exists(), r.stdout)



def worktree_ignored_immediate_cleanup():
    with fixture() as (w, root, repo, path, fields, head, meta):
        (path / '__pycache__').mkdir(); (path / '__pycache__/cache.pyc').write_bytes(b'cache')
        w.report_task_worktree(fields, repo, head)
        gone(repo, path, fields, meta)
        check(not (path.parent / 'archive').exists(), '被忽略文件被存档')


def attachment_dispatch(mode, *, worktree=True):
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        repo = git_template(root / 'repo', {'sub/note.txt': 'committed attachment\n'})
        wd = repo / 'sub'; note = wd / 'note.txt'
        if mode == 'untracked':
            note = wd / 'new.txt'; note.write_text('untracked attachment\n')
        elif mode in {'modified', 'staged', 'index_only'}:
            note.write_text('modified attachment\n')
            if mode != 'modified':
                git(repo, 'add', 'sub/note.txt')
            if mode == 'index_only':
                note.write_text('committed attachment\n')
        elif mode == 'binary':
            note.write_bytes(b'bad\x00attachment')
        prompt = root / 'prompt'; prompt.write_text('test')
        state = root / 'state'
        branches = git(repo, 'for-each-ref', 'refs/heads')
        trees = git(repo, 'worktree', 'list', '--porcelain')
        r = inprocess_sh([sys.executable, str(REPO / 'ai_worker.py'), 'codex', str(wd), '10',
                          str(prompt), '--no-findings', '--no-fallback', '--attach', str(note),
                          *(['--worktree'] if worktree else [])],
                         env=dict(os.environ, AIW_STATE=str(state), FAKE_CODEX='ok'))
        if worktree and mode != 'committed':
            message = ('附件是二进制文件' if mode == 'binary' else
                       f'--worktree 只能附加已提交的文件：{note}，请先提交或去掉 --worktree')
            check(r.returncode == 64 and message in r.stderr, r.stderr)
            check(not (state / 'worktrees').exists(), '预检失败创建了目录/元数据')
            check(git(repo, 'for-each-ref', 'refs/heads') == branches, '预检失败创建了分支')
            check(git(repo, 'worktree', 'list', '--porcelain') == trees, '预检失败创建了 worktree')
            check(not (state / 'seat-ledger.jsonl').exists(), '预检失败仍派单')
        else:
            check(r.returncode == 0, r.stderr)
            row = json.loads((state / 'seat-ledger.jsonl').read_text().splitlines()[-1])
            task = (Path(row['run_dir']) / 'task.txt').read_text()
            check('note.txt' in task if mode == 'committed' else 'new.txt' in task, task)
            check(hashlib.sha256(note.read_bytes()).hexdigest()[:12] in task, '附件内容与源不一致')
            check(('worktree' in row) == worktree, str(row))


def worktree_attach_untracked_rejected():
    attachment_dispatch('untracked')


def worktree_attach_modified_rejected():
    attachment_dispatch('modified')


def worktree_attach_staged_rejected():
    attachment_dispatch('staged')
    attachment_dispatch('index_only')


def worktree_attach_source_validation():
    attachment_dispatch('binary')


def worktree_attach_committed():
    attachment_dispatch('committed')


def attach_untracked_without_worktree():
    attachment_dispatch('untracked', worktree=False)


CASES = {fn.__name__: fn for fn in (
    worktree_ignored_immediate_cleanup, worktree_attach_untracked_rejected,
    worktree_attach_modified_rejected, worktree_attach_staged_rejected,
    worktree_attach_source_validation, worktree_attach_committed, attach_untracked_without_worktree,
    worktree_empty_dispatch_cleanup, worktree_dirty_metadata, worktree_merged_cleanup,
    worktree_recent_retained, worktree_expired_binary_restore, worktree_archive_failure_retained,
    worktree_locked_skipped, worktree_missing_source_cleanup, worktree_missing_directory_prune,
    worktree_gc_dry_run_and_patches,
)}
