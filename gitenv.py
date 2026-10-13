import os


def repository_git_env() -> dict:
    """按 cwd 定位仓库，避免调用方的 Git 仓库定位变量干扰。"""
    excluded = {
        "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_CEILING_DIRECTORIES", "GIT_DISCOVERY_ACROSS_FILESYSTEM",
        "GIT_NAMESPACE", "GIT_PREFIX", "GIT_SHALLOW_FILE", "GIT_GRAFT_FILE",
    }
    return {key: value for key, value in os.environ.items() if key not in excluded}

