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


def canonical_key(key, aliases=None):
    """Normalize repository identities in mapping keys and complete marker lines."""
    import re
    aliases = ALIASES if aliases is None else aliases

    def repo(value):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', value):
            return value
        for old, new in aliases.items():
            if value.lower() == old.lower():
                return new.lower()
        return value

    if key.startswith('aiw-key: '):
        return 'aiw-key: ' + canonical_key(key[len('aiw-key: '):], aliases)
    if '#' in key:
        identity, suffix = key.split('#', 1)
        return repo(identity) + '#' + canonical_key(suffix, aliases)
    if key.startswith('project:'):
        identity, separator, suffix = key[len('project:'):].partition(':')
        return 'project:' + repo(identity) + separator + suffix
    return key


def legacy_key(key):
    return canonical_key(key, {new: old for old, new in ALIASES.items()})


def normalize_record(row):
    if 'repo' in row:
        return dict(row, repo=canonical_repo(row['repo']))
    return row
