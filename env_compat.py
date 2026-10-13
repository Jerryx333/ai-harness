"""Read aiw environment settings with Python and state path defaults."""
import os


def getenv(name, default=None):
    value = os.environ.get(name, default)
    if name == 'AIW_PYTHON':
        return value or 'python3'
    if name == 'AIW_STATE' and value is not None:
        return os.path.expanduser(value)
    return value
