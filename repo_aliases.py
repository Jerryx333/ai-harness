"""Repository rename aliases, applied on reads without rewriting records."""
ALIASES = {'Jerryx333/ai-harness': 'Jerryx333/aiw'}


def canonical_repo(repo):
    if not isinstance(repo, str):
        return repo
    for old, new in ALIASES.items():
        if repo.lower() == old.lower():
            return new
        if repo.lower() == 'github.com/' + old.lower():
            return 'github.com/' + new.lower()
    return repo


def canonical_key(key):
    for old, new in ALIASES.items():
        key = key.replace(old, new).replace(old.lower(), new.lower())
    return key


def normalize_record(row):
    if 'repo' in row:
        return dict(row, repo=canonical_repo(row['repo']))
    return row
