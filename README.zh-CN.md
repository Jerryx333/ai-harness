# ai-harness

[English](README.md) | [中文](README.zh-CN.md)

让 Claude Code / Codex 主模型把精力留给规划和把关，把读代码、跑命令、起草摘要这类体力活交给 Codex 或更便宜、已闲置的模型。装上就能用，不用起服务、不装依赖。

## 解决什么问题

- 主模型额度不够用：订阅有时间窗口和周限额，活干到一半就停；读大文件、翻日志、跑测试这类活很耗额度。
- 上下文被撑爆：主会话塞满文件内容和命令输出后会被压缩、忘细节、出错。
- 已有的订阅和免费模型闲着：手里有 Codex/ChatGPT 订阅或免费模型，却只有一个主会话在干活。

## 装上之后有什么不同

- 主模型额度更耐用：体力活派出去，主模型把额度花在规划和验收上。
- 主会话上下文更干净：派出去的活在别处读文件、跑命令，主会话只收结果。
- 多一道交叉评审：改代码和评审可以由不同模型做；`aiw panel` 收集多家独立意见。
- 不用改习惯：照常用 Claude Code 或 Codex；遇到适合派出去的活，它会提示这件事该交给谁，派不派由主会话决定。
- 某家不可用时自动换下一家，活不会卡住；默认只有一家，换谁、按什么顺序在[本地配置](docs/guide.zh-CN.md#自定义)里定。

## 想接更多模型？用 agent-use

手里有别家的订阅、本地模型或免费模型，想让它们也分担工作，常见的麻烦是：

- **每家用法都不一样**，接一个就得单独折腾一套。用 [agent-use](https://github.com/Jerryx333/agent-use)，任何能在终端里用的模型都按同一种方式接进来，加几行配置就行，不用写代码。
- **某家一挂、一限流，活就卡住。** agent-use 会按你排好的顺序自动换下一家，活不会断。
- **别的模型交回来的东西靠不靠谱？** 每次都留存原文和过程记录，结果一律当待审稿，由主会话核对后才算数。

接入步骤见[使用指南](docs/guide.zh-CN.md#接入更多模型可选)。

## 你可能会担心的

**质量会不会降？** 派出去的产出都是待审稿，主会话核对文件、证据和测试结果后才算数。

**派单本身也花主模型 token 吧？** 是的：写要求、读结果、核对都要花。很小的任务不一定划算，所以只提示、不自动派。想看自己的实际用量：`aiw tokens`（本机 Claude Code 主会话用量和上下文构成）、`aiw weekly`（派单成功率和 token 汇总）。

**会不会花钱？** Codex、Claude 按你自己的账号计费。接入的免费模型可以标成只用免费档，一旦检测到收费会自动停用；但这是调用之后才检测，不保证零费用。

**代码会发到哪里？** 交给哪个模型，任务要求和附带材料就发给那个模型背后的服务。只负责写文字的模型默认看不到你的代码，要你明确附上才会发过去。密码、密钥和私人数据不要交给第三方模型。

**只有 Claude Code、没有 Codex 能用吗？** 能安装，但默认改代码座位 `codex` 和只读评审座位 `codex-sol` 依赖 Codex CLI。默认的 Claude 主会话工作流因此会打折。

**支持 Linux 吗？** 尚未测试。已测试 macOS。

**规则太强势怎么办？** 用个人规则覆盖，或从 `~/.claude/CLAUDE.md` 删除 `@~/.claude/ai-harness/CLAUDE.md`。若也不想收到派单提示，按[卸载小节](docs/guide.zh-CN.md#卸载)移除 harness 钩子。

## 5 分钟上手

需要 macOS（目前只在 macOS 测试过）、Python 3，以及已登录的 Claude Code 和/或 Codex CLI（终端程序）；可选 [agent-use](https://github.com/Jerryx333/agent-use)，用于接入更多模型。

1. 克隆到此固定位置，再安装：

   ```bash
   git clone https://github.com/Jerryx333/ai-harness.git ~/.claude/ai-harness
   ~/.claude/ai-harness/install.sh
   ```

   安装会改哪些文件、怎么备份，见[使用指南](docs/guide.zh-CN.md#安装做了什么)。

2. 按官方方式登录各 CLI：`codex login`；或运行 `claude`，输入 `/login`。PATH 有变化时新开终端，然后运行：

   ```bash
   aiw doctor
   ```

   配置警告不改变 doctor 退出码。Codex 下次启动可能要求信任新钩子。

3. 第一单：只读查看本仓库，需要 Codex：

   ```bash
   printf '%s\n' '阅读本仓库，说明入口，并引用文件位置。不要修改文件。' > /tmp/aiw-review.txt
   aiw codex-sol "$HOME/.claude/ai-harness" 900 /tmp/aiw-review.txt --ro
   ```

4. 之后照常用 Claude Code 或 Codex。钩子提示怎么派，主会话决定方案并验收。

## 常用命令

`.` 是项目目录，`task.txt` 是任务要求，超时单位为秒。先准备输入文件。

| 命令 | 用途与示例 |
| --- | --- |
| `aiw <座位> <目录> <秒数> <prompt文件> [--ro] [--net]` | 派一单；`--net` 仅为 Codex 写单开放网络和本地端口，默认关闭；与 `--ro` 或非 Codex 座位同用返回 64。`--ro` 要求只读：`aiw codex-sol . 900 task.txt --ro`。 |
| `aiw panel` | 收集独立意见：`aiw panel . 600 task.txt --prior prior.md`；默认最多 4 条、1500 字，每条写结论、证据（没有则「无证据」）、影响或建议；stdout 内联回答（每家最多显示 3000 字并给出全文路径），全文归档。`--full` 不追加格式要求，stdout 不内联各家回答（全文在会诊目录的 `<座位>.md`）；Claude Code 里自动识别主会话；Codex 主会话需加 `AIW_MAIN=codex` 前缀；排除主会话座位，默认可能只剩一家评审。先写自己的判断用 `--prior` 传入；看完逐条标接受/驳回（理由）/暂缓（写明等什么），不以多数票收敛。 |
| `aiw map` | 对每个文件执行同一要求：`aiw map . 600 task.txt src/a.py src/b.py`；默认用 `codex-sol`，最多并发 3 单。 |
| `aiw goal init / verify` | 用 `aiw goal init docs/goals/demo.json` 建清单；修改示例任务、`verify` 命令和 `paths`，再跑 `aiw goal verify docs/goals/demo.json --full`。 |
| `aiw doctor` | `aiw doctor` 检查座位和安装；`aiw doctor --fingerprint` 输出一行配置指纹，即版本和哈希摘要，便于比较。 |
| `aiw note` | 保存观察：`aiw note . "检查失败" --expected "检查通过" --evidence "测试输出"`。 |
| `aiw sync` | `aiw sync` 用 `git pull --ff-only` 更新 harness 仓库，不能合并分叉历史；更新已安装规则/钩子需重跑安装。 |
| `aiw gc` | 删除旧的派单、会诊、批处理归档，默认保留 30 天；先预览：`aiw gc 30 --dry-run`。 |

## 更多文档

- [使用指南](docs/guide.zh-CN.md)：安装细节、工作原理和定制。
- [中文参考手册](docs/reference.zh-CN.md)：全部命令与细则。
- [改进缓冲区](docs/findings.md)
- [状态约定](docs/state-contract.md)
- [红线与回归检查](docs/invariants.md)

## 参与贡献

运行 `python3 tests/test_harness.py`（后台低优先级；默认并发为 CPU 数的四分之一（至少 1），`-j N` 硬上限为 CPU 数的一半（至少 1），`-j 1` 串行，`--durations` 列出最慢用例）和 `python3 tests/mutation_check.py`，改动走 PR。用例位于 `tests/cases/`，公共辅助位于 `tests/support.py`。许可证：[Apache-2.0](LICENSE)。
