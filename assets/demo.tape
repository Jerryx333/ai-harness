# aiw 终端演示。重新生成：在仓库根目录运行 `vhs assets/demo.tape`。
# 需要：已安装 aiw（PATH 里有 aiw）、已登录 Codex CLI 或 Claude Code、jq；
# 中文显示用 Maple Mono CN 字体（brew install --cask font-maple-mono-cn），没有时中文字距会偏宽。
# 用临时演示项目和空的本地配置，只展示公开默认配置；会真实派一单只读评审。
# 等待执行方的那段不录（Hide/Wait/Show），输出里的临时目录显示为 ~/demo。

Output assets/demo.gif

Require aiw
Require jq

Set Shell "bash"
Set FontFamily "Maple Mono CN"
Set FontSize 17
Set Width 1360
Set Height 720
Set Padding 22
Set Theme "Catppuccin Mocha"
Set TypingSpeed 35ms
Set Framerate 20

Hide
Type `export D=$(cd "$(mktemp -d)" && pwd -P) && mkdir -p "$D/proj" && cd "$D/proj" && echo '{}' > "$D/seats.json" && echo '{"rules":[]}' > "$D/rules.json"`
Enter
Type `export AIW_CONFIG="$D/seats.json" AIW_ROUTER_RULES="$D/rules.json" AIW_STATE="$D/state"`
Enter
Type `printf 'import sys\n\n\ndef average(values):\n    return sum(values) / len(values)\n\n\ndef parse(argv):\n    return [int(x) for x in argv[1:]]\n\n\nif __name__ == "__main__":\n    print(average(parse(sys.argv)))\n' > calc.py`
Enter
Type `printf '%s\n' '评审 calc.py：列出最多 3 个会出错的边界情况，每条一行：行号 — 问题 — 修法。不要修改文件。' > task.txt`
Enter
Type `aiw() { command aiw "$@" 2>&1 | sed "s#$D#~/demo#g"; }; export PS1='\[\e[1;35m\]$ \[\e[0m\]'; clear`
Enter
Show

Sleep 400ms
Type "# 每条消息都会被判断：这件活该派给哪个角色？"
Enter
Sleep 300ms
Type `aiw route "给 config 模块补单元测试" | jq -r .seat`
Enter
Sleep 1200ms
Type `aiw route "起草这次改动的 PR 描述" | jq -r .seat`
Enter
Sleep 1500ms

Type "# 角色按顺序解析到第一个可用的座位（模型/CLI）"
Enter
Sleep 300ms
Type `aiw doctor | grep '角色 '`
Enter
Sleep 2500ms

Type "# 派一单只读评审：主会话只收结果，过程留档在 run 目录"
Enter
Sleep 300ms
Type "cat task.txt"
Enter
Sleep 1200ms
Type "aiw review . 600 task.txt --ro"
Enter
Sleep 1500ms
Hide
Wait+Screen@600s /全文/
Show
Sleep 9s
