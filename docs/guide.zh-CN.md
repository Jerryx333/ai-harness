# aiw 使用指南

先看 [README](../README.md) 了解它解决什么问题；本文讲安装细节、工作原理和定制。

## 安装做了什么

安装脚本会做这些改动：

- `~/.claude/CLAUDE.md`：添加公开规则引入行；个人规则存在时，紧接着引入。保留其他文字。
- `~/.claude/settings.json`：添加或刷新 `UserPromptSubmit` 派单钩子。保留其他设置和钩子。
- `~/.local/bin/aiw`：创建或替换指向本仓库的软链，**不备份**。
- `~/.zshrc`：仅当 PATH 缺少 `~/.local/bin` 且文件没有安装标记时，追加带标记的 PATH 设置。
- 若存在 `~/.codex` 或 PATH 中有 `codex`：更新 `~/.codex/AGENTS.md` 的 aiw 标记块，以及 `~/.codex/hooks.json` 的钩子。保留块外文字。设 `AIW_NO_CODEX=1` 可跳过。
- 创建仓库的 `state/` 目录，给入口脚本加执行权限，检查路由并运行 doctor。检查可能只警告，不使安装失败。

已有文件改动前备份为 `.bak-年月日-时分秒`，新建或未变的不备份。先检查 JSON；重复安装不重复追加。`--claude-hook=plugin` 会移除 settings 中的派单钩子，交给另行管理的插件；它不安装插件。

## 它是怎么工作的

aiw 帮 Claude Code 或 Codex 主会话把活分给不同模型：改代码派 Codex，只读评审派另一个模型，起草和摘要派可选的便宜或免费模型。主会话规划和验收；Codex 主会话也自己改代码。**座位**就是一个可派单的模型或命令行工具。顶替链让失败的任务换下一家；熔断暂时跳过不可用座位。不是框架或服务：规则文件和仅用标准库的 Python 脚本。

`codex`/`codex-sol` 需 Codex CLI，`claude` 需 Claude Code；CLI 即终端程序。

- `aiw`：统一的派单命令。
- 派单钩子：在 Claude Code 和 Codex 里提示任务该派给谁，不会自动派单。
- `aiw panel`：多模型会诊，即收集多个座位的独立意见。返回码 0 要求至少两家成功且有产出（`rc == 0` 且 `chars > 0`），不足两家返回 1；单家仍执行并展示结果，但不构成会诊。
- `aiw goal`：带可执行验收命令的任务清单。
- 改进缓冲区：保存观察，集中分析根因。
- `aiw doctor`：检查座位安装和配置问题。

```text
主会话 → 派单钩子提示 → 主会话运行 aiw → 座位
                                          ↓ 失败
                                按配置的顶替链换下一家
                                / 暂时熔断
产出 → 主会话核对文件、证据和测试
```

产出都是**待审稿**。成功时，stderr 的「实际执行方」行报告实际座位及 `provider` 文件的实际路径，可核对顶替。默认写在 run 目录；设置 `AIW_PROVIDER_FILE` 时写到指定路径。Git 仓库根目录与子目录的写单共用一把锁。Git 写单开工前保存 `baseline-status.txt`，结束时分列开工前已有改动与本单期间新出现变化的文件。默认只有 `codex`、`codex-sol`、`claude`，顶替链为空，需本地配置。文本座位需显式用 `--attach 文件` 才能看到仓库材料。附件按实际座位分发：CLI 座位只收通过安全校验的文件清单（相对路径、字节数、SHA256 前 12 位），在工作目录自行读取；其它座位收内联全文。两种方式均保留 200KB 上限及安全检查；文本座位附件超过 30KB 只警告不拒绝，直接派单或 panel 各只提示一次。附件内容会原样发给对应座位背后的模型服务；安全检查只拦凭据类文件、越界路径和二进制等，不识别正文里的密码、密钥或私人数据，发送前请自行确认。

## 接入更多模型（可选）

使用公开的 [agent-use 核心](https://github.com/Jerryx333/agent-use)。若不装在 `~/code/ai/agent-use`，用 `AGENT_USE_HOME` 指定其仓库位置。

1. 在 `~/.config/agent-use/dispatcher.json` 注册 **provider**，即调用模型/工具的适配器。可用 `examples/cli_adapter.py` 包装已有 CLI，参照其注册表示例。任务送到标准输入，标准输出即答复；工具需另行安装和登录。
2. 在 `~/.config/aiw/seats.local.json` 加匹配的座位，替换 provider 和 model 占位值：

   ```json
   {
     "seats": {
       "local-text": {
         "kind": "agent_use",
         "provider": "your-provider",
         "model": "your-model"
       }
     },
     "fallback": {"local-text": []}
   }
   ```

3. 可选：用 `router-rules.local.json` 将起草类任务路由过去；参照[座位示例](../seats.local.example.json)和[路由示例](../router-rules.local.example.json)。
4. 运行 `aiw doctor`。启用座位的 provider 在生效注册表中缺失或被禁用时会警告；这类警告不改变退出码。

## 自定义

本地文件放在 `~/.config/aiw/`：

| 文件 | 管什么 |
| --- | --- |
| `seats.local.json` | 座位、模型、顶替链、会诊名单等本地设置。 |
| `router-rules.local.json` | 哪种任务描述提示派给哪个座位。 |
| `CLAUDE.personal.md` | 在公开 Claude 规则之后引入的个人规则。 |
| `AGENTS.personal.md` | 安装时复制到公开 Codex 规则之后的个人规则。 |

冲突时以个人规则为准。新增个人文件或修改 Codex 规则后重跑 `install.sh`。项目路由放在 `.claude/router-rules.json`，优先级是项目 > 本地 > 公开。座位配置的对象递归合并，列表整体替换。

## 多台机器

每台都克隆同一个公开仓库。本地配置和个人规则放进自己的私有仓库，软链到 `~/.config/aiw/`。比较两台的 `aiw doctor --fingerprint`；登录和运行状态各机独立。

## 卸载

无卸载选项。手动撤销以下改动，保留其他设置：

- 从 `~/.claude/CLAUDE.md` 删除 aiw 引入行及其个人规则引入行。
- 从 `~/.claude/settings.json` 和已接入时的 `~/.codex/hooks.json` 中，仅删除命令含 `aiw/route.py` 的钩子，保持 JSON 合法；从 `~/.codex/AGENTS.md` 删除 `aiw:begin` 到 `aiw:end` 的整个标记块。
- 删除 `~/.local/bin/aiw` 软链；若安装时添加过，从 `~/.zshrc` 删除 `# aiw: on PATH` 及其 PATH 设置。
- 停用另行安装的插件钩子。不再需要时才删除仓库（含本地 `state/`）和本地 aiw 配置。也可用 `.bak-*` 恢复代替手改，但须确认不会丢掉后续修改；命令软链没有备份。
