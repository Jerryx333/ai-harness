# bash 3.2: distinguish unset from empty; normalize once for Python children.
aiw_env_compat() {
  local new old nv ov
  for new in AIW_STATE AIW_PYTHON AIW_NO_CODEX; do
    old="AI_HARNESS_${new#AIW_}"
    if eval '[ "${'"$new"'+set}" = set ]'; then
      eval 'nv=${'"$new"'}'
      if eval '[ "${'"$old"'+set}" = set ]'; then
        eval 'ov=${'"$old"'}'
        if [ "$nv" != "$ov" ] && [[ ":${_AIW_ENV_WARNED:-}:" != *":$new:"* ]]; then
          printf '警告：%s 与 %s 值不同，使用 %s；重跑 install.sh 并迁移旧变量\n' "$new" "$old" "$new" >&2
          export _AIW_ENV_WARNED="${_AIW_ENV_WARNED:+${_AIW_ENV_WARNED}:}$new"
        fi
      fi
    elif eval '[ "${'"$old"'+set}" = set ]'; then
      eval 'nv=${'"$old"'}'
      export "$new=$nv"
    fi
  done
}
aiw_env_compat
