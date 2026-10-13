# 改进缓冲区（findings）

## 为什么

单个执行方看到的问题往往只是小视角的症状。顺手修掉症状，线索就没了，根因换个地方再冒出来。缓冲区把小视角的观察保留下来，攒成批，由主会话合起来推断根因，修在根因上（多半是规则、派单模板、工具、流程），让一整片症状一起消失。直接动因：多台机器做同一批项目时的重复修改、修改冲突、规则要手动回流。

两路证据：

1. **执行方观察**：worker 在派单 prompt 末尾追加必答问题，执行方在回答末尾写记录块，worker 解析入库。
2. **机械抽取**：从账本、run 目录、规则文件 git 历史里离线算出执行方看不到的跨任务信号。

评审用三本账：观察（不可变）、假设（跨批次累积，带可证伪预测）、验证（回归探针 + 预期信号）。

## 修改边界：禁止静默修改

| 情况 | 做法 |
|---|---|
| 本单范围内，或挡住本单验收 | 可以修，必须在记录块 `fixed:` 写明改了什么、原本应是什么 |
| 范围外的文件 | 只记不修（也防止扩大范围造成并发冲突） |
| 规则、派单模板、工具（CLAUDE.md、AGENTS*.md、router-rules.json、seats.json 等） | 一律只记，交主会话评审 |
| 安全、数据破坏 | 不等批次，当场停下在回答正文里报告 |

## 配置（seats.json `findings`）

```json
"findings": {
  "collect": true, "sync": true, "hint": true,
  "hub": "your-owner/your-findings-repo", "owners": ["your-owner"],
  "max_items": 5, "daily_cap": 300, "field_max": 300,
  "trigger_count": 100, "trigger_days": 3, "sync_interval": 3600
}
```

三个开关互相独立：`collect=false` 不追加、不解析（产出原样交付）；`sync=false` 不上传（本机照常记录，积压不会在关闭期间上传）；`hint=false` 钩子不提示。缺省整个 `findings` 段等同全关。

## 状态文件（`$STATE/findings/`）

| 文件 | 写入方 | 内容 |
|---|---|---|
| `records.jsonl` | worker、`aiw note` | 观察记录，只追加，flock 保护；同步成功后压缩：已上传的自有记录删除，只留未上传和非自有未处理的 |
| `archive/<YYYY-MM>.jsonl.gz` | sync | 非自有记录处理完（或上月及更早的回答、改动记录）后 gzip 归档，长期保留 |
| `quarantine.jsonl` | worker | 命中密钥特征的记录，永不上传 |
| `daily-<YYYY-MM-DD>.count` | worker | 当日已收观察条数（超 daily_cap 只计数） |
| `sync.json` | `aiw findings sync` | 已上传到的字节偏移、上次同步时间、最近错误 |
| `count-cache.json` | sync / triage | 两台合计待处理数、上次评审时间，钩子只读它 |
| `hub/` | sync | 集中仓库的本地 clone |
| `local-status.jsonl` | triage apply | 非自有仓库记录的处理状态（只留本机） |
| `local-hypotheses.json` / `local-hypotheses-log.jsonl` | triage apply | 解释了非自有记录的假设（只留本机） |
| `issues.json` | triage | `<实际目标仓库>#<key>` → issue 编号映射（幂等） |
| `project-issues.json` | triage issues | 项目 issue 映射键 → `{target, number, records}`，供未解释池显示转交去向（原子写） |
| `signals.json` | `aiw findings extract` | 机械抽取的信号快照，可随时重算 |
| `triage/<id>/` | triage | 批次材料 batch.md / batch.json |
| `hint.json` | 钩子 | 上次提示时间（节流） |

## 记录格式

每条一行 JSON。公共字段（worker 自动填，不靠执行方）：

`id`（`<主机短名>-<YYYYMMDDHHMMSS>-<pid>-<序号>`）、`ts`、`host`、`session`、`source`（worker/main）、`type`（answers/observation/changes）、`seat`、`actual`、`via`、`ro`、`prompt_id`、`run_dir`、`repo`（git remote origin 归一化为 `host/owner/name`，无 remote 为 null）、`workdir`（仅 repo 为 null 时填绝对路径，用于区分本机不同目录）、`repo_rel`（workdir 相对仓库根）、`commit`、`branch`、`owned`（repo 的 owner 在 `owners` 里）、`rules_hash`（aiw 的 CLAUDE.md、AGENTS.harness.md、router-rules.json、seats.json 内容 sha1 前 12 位）、`partial_context`（kind=agent_use 的座位为 true）。

- `type=answers`：每个收集了记录的成功派单一条，字段 `guess`、`deviation`、`abandoned`、`fixed`（执行方回答，「无」原样保留）。它同时是比例的分母。
- `type=observation`：执行方每条观察一条，字段 `observation`、`expected`、`evidence`、`location`（可选）。
- `type=changes`：写任务成功时一条，字段 `files`（相对仓库根的改动文件列表，来自 WorkChanges），用于算跨派单的文件重叠。

`owned=false` 的记录只留本机：不上传，处理状态记在 `local-status.jsonl`。

## 记录块协议

worker 追加到 prompt 末尾的说明（`prompt_id` 在追加前计算）：

```
---
【aiw 观察记录】完成上面的任务后，在回答最末尾附下面的记录块。它不影响验收，只用于改进流程，请如实写，没有就写「无」。
修改边界：本单范围内或挡住本单验收的问题可以修，但必须在 fixed 写明改了什么、原本应是什么；范围外的文件只记不修；规则、派单模板、工具一律只记不修；安全或数据破坏问题当场停下在正文报告。
<<<AIW-FINDINGS
guess: 哪一步是你猜的、说明里没给的？
deviation: 实际步骤和原计划有什么不同，为什么？
abandoned: 试过又放弃的做法和原因
fixed: 范围内修了什么（改了什么 / 原本应是什么）
- observation: 现象 | expected: 你以为应怎样、依据是什么 | evidence: 命令、输出或 文件:行 | location: 路径:函数
AIW-FINDINGS>>>
```

`- observation` 行最多 `max_items` 条，可以没有。

**何时追加**：`collect=true`，且 `via=direct`（panel、map 不追加），且没带 `--no-findings`，且不是「kind=agent_use 座位并且没有 `--attach`」（纯文本活看不到代码）。

**解析**：只认输出末尾最后一个完整块，且块内至少有一个已知字段。结束标记后允许不超过 3 个非空行、去掉首尾空白后不超过 300 字符且不含 `<<<AIW-FINDINGS` 的尾随正文；接受时保留尾文，并在 `parse` 第三个返回值给出非致命提示（解析结果仍存在，调用方照常入库）。超过上限维持「记录块后还有正文」拒绝行为。找到则从交付给主会话的产出里剥掉；`output.md` 保留原文。剥掉后正文为空，按 `empty` 处理（rc=2）。块不完整、格式坏、解析或记录过程出任何异常：不剥离、不入库、stderr 警告一行，不影响 rc、顶替、熔断。记录锁最多等 10 秒；进程被杀留下的半行会在下次追加前补换行，坏行在读取时跳过。只有 kind=ok 的尝试入库。

**清洗**：每个字段截到 `field_max` 字；去掉代码围栏；只对自由文本字段（四个回答字段和 observation/expected/evidence/location）做密钥扫描，元数据不扫（记录 id、run 目录这类串熵很高，会误报）；密钥特征（`sk-`、`ghp_`/`gho_`/`github_pat_`、`AKIA`、`AIza` 后接 35 位 `[0-9A-Za-z_-]`、`xox[bap]-`、`-----BEGIN`、`eyJ` 开头的 JWT、高熵串）命中则整条进 quarantine，stderr 提示。高熵规则保持不变：只检查长度 ≥32 的候选串，沿用原有路径拆分条件，熵 ≥4.0 即命中，纯十六进制摘要仍排除。专用前缀检查之后、高熵扫描之前，仅将三种 aiw 生成的完整标识符替换为 `x`（保持候选串连续）；主机名、座位名只认已知字面值，由不可变扫描上下文固定：每次 `record(rows)` 在取得 `records.lock` 后、扫描行之前构造一次；每次合法对象的 `triage.validate(data, …)` 在首次递归扫描前构造一次，全部字段及字典键复用（测试可显式注入上下文）。`has_secret(text, context)` 显式接收上下文，扫描不读取环境、不做进程级缓存；不同调用或机器仍各自读取当前环境：记录 id（`<主机名>-<YYYYMMDDHHMMSS>-<pid>-<序号>`，主机名来自本机 `socket.gethostname().split(".")[0]` 与本机 hub clone 的 `raw/` 子目录名的并集，目录不存在或读失败则忽略）、run 目录名（`<YYYYMMDD>-<HHMMSS>-<座位名>-<pid>-<8 位小写十六进制>`，座位名来自 `load_seats()` 合并配置，读取失败则视为空集合）、panel 目录名（`<YYYYMMDD>-<HHMMSS>-<pid>`）。已知名称经 `re.escape` 转义并按长度降序交替；主机名或座位名集合为空时不剥离对应标识符。可变部分只允许数字和十六进制：时间分别为 14 位或 8 位加 6 位数字，pid 为 1–7 位数字，记录序号为 1–4 位数字。精确正则两侧禁止紧邻 `[A-Za-z0-9_-]`，避免剥离更长随机串中的片段；其他路径片段等仍可能误判，进入隔离区人工复核。明确密钥前缀另按专用正则判定，`AIza` 规则为 `\bAIza[0-9A-Za-z_-]{35}(?![0-9A-Za-z_-])`，末尾使用字符集边界以检测以 `-` 或 `_` 结尾的密钥。

**回报主会话**：派单结束时 stderr 输出一行 `[ai-worker] 观察记录：N 条`；若 `fixed` 不是「无」，再输出 `[ai-worker] 范围内修改：<fixed>`，供主会话验收。账本行加 `findings_n`（观察条数，未收集时无此键）。

## aiw note

`aiw note <workdir> <现象> [--expected 文本] [--evidence 文本] [--location 文本]`：主会话记一条 `source=main` 的 observation，公共字段按 workdir 计算。

## aiw findings

- `aiw findings extract [--days N]`：机械抽取，写 `signals.json`，并打印摘要：
  - **文件重叠**：同一 repo 同一文件在 24 小时内被 ≥2 个不同派单（changes 记录，含集中仓库里另一台的）改动。
  - **规则振荡**：aiw 仓库里 CLAUDE.md、AGENTS.harness.md、router-rules.json、seats.json 在窗口内按 `## ` 小节统计修改次数，≥3 次列出。
  - **重复手写指令**：窗口内 run 目录 `task.txt`（去掉附件和记录块说明）里，长度 ≥15 字的同一行出现在 ≥5 个不同 prompt 中。
- `aiw findings sync [--quiet]`：见下。
- `aiw findings status`：本机未上传数、待处理数、quarantine 数、上次同步和评审时间。

## 同步

worker 每次派单结束，若距上次同步超过 `sync_interval`，后台起一个脱离的 `aiw findings sync --quiet`（不等它，不影响 rc）。sync 用 `state/findings/sync.lock` 互斥：

1. 确认 `hub/` 存在（不在则 `gh repo clone`），`gh repo view --json visibility` 必须是 PRIVATE（每天查一次缓存），否则拒绝并记错误。
2. 把 `records.jsonl` 里偏移之后、`owned=true` 的记录追加到 `hub/raw/<主机短名>/<YYYY-MM>.jsonl`（按 id 跳过已存在的），commit，push。hub clone 由工具独占：每次写入前 `fetch` + `reset --hard` 到远端，push 被拒就重新 reset 后重放修改，最多 3 次；triage 的写入走同一个事务函数。push 成功才推进偏移；失败保留偏移、记错误。
3. 重算 `count-cache.json`：集中仓库所有 raw 里未出现在 `hub/state/status.jsonl` 的 observation 数 + 本机 owned=false 未处理数；上次评审时间取 `hub/state/triages.jsonl` 最后一行。

原始记录内容永不修改；处理状态只追加到 `hub/state/status.jsonl`。满 12 个月的 `raw/` 文件在同步时移到 `archive/raw/`，本机 clone 用 partial clone + sparse checkout 不检出 `archive/`，统计与评审只读最近 12 个月。本机不长期保留已上传的记录。

## 触发提示

`route.py --hook` 在 `hint=true` 时读 `count-cache.json`：待处理 ≥ `trigger_count`，或待处理 >0 且距上次评审 ≥ `trigger_days` 天，就在注入内容后追加 `[缓冲区] 待处理 N 条，上次评审 X 天前，建议运行 aiw triage start`。同一台机器 4 小时内最多提示一次。钩子不联网、不跑 git。

## 评审 aiw triage

- `aiw triage start`：先 sync；在集中仓库提交 `triage.lock`（主机、开始时间、2 小时后过期）并 push，push 被拒（另一台已持锁）且锁未过期则返回 75；然后跑 extract，生成 `triage/<id>/batch.md` 与 `batch.json`：
  1. 概况：各主机记录数、各座位 answers 数（分母）与观察率、「无」回答比例、各主机最新 rules_hash（不一致标出）。
  2. 假设台账里未关闭的假设（状态、预测、验证窗口、到期提醒、回归探针）。
  3. 机械信号（extract 结果）。
  4. 机械分组：按 repo、目录、座位、主机、rules_hash 分别分组计数；关键词（找不到、不一致、被覆盖、冲突、超时、格式、猜、手动、重复、过期、权限、绕过）频次。只做字面统计，不做语义归类。
  5. 未解释池（以往标为 unexplained、60 天内）与本批全部待处理记录（id | 来源 | 座位 | repo | 正文）。
- 主会话读 batch.md，写 `decisions.json`，`aiw triage apply <decisions.json>` 校验后执行：
  - 假设 upsert 到 `hub/state/hypotheses.json`；
  - 每条记录的处理状态追加到 `hub/state/status.jsonl`（owned=false 的写 `local-status.jsonl`）；
  - 要开的 issue 在集中仓库创建，正文带 `aiw-key: <key>`，创建前按 key 搜索已有 issue，已存在则跳过（幂等）；
  - `project_issues` 将项目缺陷转交到 PRIVATE 且 issues 开启的项目仓库；PUBLIC/INTERNAL、issues 关闭或查询失败时改开在集中仓库，标题加 `[<name>] `、标签加 `project:<name>`，stdout 说明原因；正文末尾附 `aiw-key: project:<小写 owner/name>:<key>` 与 `aiw-records: id1, id2`；
  - 对本次自有且标为 `unexplained`、未被 `project_issues` 引用的记录打印 id 与仓库提示；未解释池中已转交的记录行末显示 `→ <target>#<number>`；
  - 追加 `hub/state/triages.jsonl` 一行，删除 `triage.lock`，push。
- `aiw triage abort`：删除自己持有的锁。

评审的远端写入（start、apply、issues、abort）要求 `sync=true` 且仓库可见性为 PRIVATE，否则拒绝。隐私边界：`explains` 含非自有记录的假设只存本机 `local-hypotheses.json`；根因 issue 必须指向一个存放在集中仓库的假设（`issues[].hypothesis`）；可选的 `project_issues` 无需假设，只能引用自有记录；所有将要上传的假设字段、状态备注、issue 文本都做密钥扫描，命中即拒绝。apply 先写本机状态和本机假设，再提交集中仓库，失败时锁仍在，可直接重跑。issue 创建持有本机锁，先查 `issues.json`，再查正文含完整 `aiw-key:` 行的已有 issue。执行方（`AIW_NESTED=1`）不能运行 triage。

`decisions.json` 结构：

```json
{
  "self_cause_considered": "本批是否有症状由主会话自己的规则或派单模板引起：……",
  "hypotheses": [{
    "id": "H-20261010-1", "title": "", "mechanism": "原因如何产生这些现象",
    "explains": ["记录id"], "predicts": ["若成立还应看到"], "refuted_by": ["看到什么说明它错了"],
    "competing": ["竞争解释"], "status": "待补证|支持|反驳|试修|验证中|已验证|已放弃",
    "layer": "机制|项目规则|代码", "fix": {"commit": null, "issue": null},
    "evidence": {"kind": "已见后果", "refs": ["记录id", "path:line"]},
    "baseline": {"metric": "每 100 单 timeout 观察数", "direction": "down", "window": "2026-10-01..2026-10-07", "value": 10, "evidence": ["before.json"]},
    "comparison": {"window": "2026-10-08..2026-10-14", "value": 5, "task_mix": "可比", "evidence": ["after.json"]},
    "probe": "回归探针：一条能复现旧症状的命令或 prompt 文件", "expect_down": [], "expect_flat": [],
    "window_days": 7
  }],
  "records": [{"id": "记录id", "status": "explained|unexplained|stale|wontfix|unverifiable", "hypothesis": "H-…", "note": ""}],
  "issues": [{"key": "根因键", "hypothesis": "H-…（须存放在集中仓库）", "title": "", "body": "", "labels": ["repo:…", "layer:…", "prio:…"]}],
  "project_issues": [{"key": "项目缺陷唯一键", "repo": "owner/name", "records": ["记录id"], "evidence": {"kind": "确定缺陷", "refs": ["path:line"]}, "title": "", "body": "", "labels": []}]
}
```

校验：`self_cause_considered` 非空；每个假设 `explains`、`predicts`、`refuted_by` 非空；状态为 `试修/验证中` 的必须有 `baseline`、`probe`、`expect_down`、`window_days`；证据门槛与「已验证」的可比复验要求见下方补充校验；`records[].id` 必须存在于本批。`project_issues` 的 `key`、`repo`、`title`、`body` 非空，key 不得换行，repo 匹配 `^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$`；labels 为字符串列表，records 为非空字符串列表。key、title、body、labels、evidence 均扫描密钥，命中拒绝。apply 时引用记录须在本批 pending 或 pool 中且 `owned=true`；若本次 records 中有该记录的决定，status 必须为 `unexplained`；记录 repo 的仓库名段与目标 repo 的 name 段按大小写不敏感比较，owner 可以不同（账号改名）；仓库名不一致不拒绝，apply 在 stdout 为每条不一致的引用打印记录 id、记录仓库与目标仓库提示，允许将其它项目中发现的工具缺陷转交工具仓库。独立的 `aiw triage issues <decisions.json>` 无 batch，除格式与密钥校验外，还从集中仓库 raw 和本机 records.jsonl 读取记录，要求每个引用 id 存在且 `owned=true`。项目 issue 与根因 issue 共用本机 `issues.lock`：项目仓库现查返回的 `nameWithOwner` 为规范名：marker 中项目身份及映射键中仓库身份统一小写，实际目标使用返回的规范名（查询失败用小写输入名作为项目身份）；先查规范化的本机映射，再在实际目标仓库以 `--state all` 搜索正文中的完整 `aiw-key:` 行；单条失败继续其它条，最终返回 1 并提示用 issues 重试。

补充校验：以上为待填写的骨架，状态和层级需从列出的选项中选择一个，替换占位符并删除不需要的条目。`comparison` 示例可选，但「已验证」时必填。

- `evidence.kind` 只能为「已见后果」（记录/日志中已发生的错误结果）、「明文要求」（违反成文规则或验收条件，引用 `path:line`）、「确定缺陷」（现存且可确定复现的缺陷，如配置格式坏、密钥明文）或「线索」（次数、篇幅、搜不到、相似度、理论风险、证据不可得等，只能继续查，不能当结论）。证据类型只说明证据性质，与严重度、假设状态是独立字段。
- `evidence.refs` 为非空字符串组成的列表，引用记录 id、`path:line` 或输出文件路径，仅「线索」允许空列表。假设状态为「支持/试修/验证中/已验证」时必须提供非线索证据；「待补证/反驳/已放弃」可省略，提供则必须格式合法。根因 issue 引用的假设必须有非线索证据，优先查本批同 id 假设，否则查 hub 已有假设；旧假设缺少证据必须重新提交并补 `evidence`。`project_issues[].evidence` 在 apply 与独立 issues 模式均必填且须为非线索证据。假设的证据和基线字段随其它上传字段扫描密钥（`explains` 除外）；项目 issue 的 key、title、body、labels、evidence 均扫描密钥，命中拒绝。
- 「试修/验证中」必须有合法 `baseline`，原有 `probe`、`expect_down`、正整数 `window_days` 要求保留。`baseline.metric/window` 为非空字符串，`direction` 为 `down` 或 `up`，`value` 为有限 int/float，不能为 bool、NaN 或 inf，`evidence` 为非空字符串列表。「试修/验证中」的 `comparison` 可省略；其它状态的 `baseline/comparison` 均可省略，提供则必须合法。
- `comparison.window` 为非空字符串，`value` 同样须为有限数字，`task_mix` 为「可比/未核实/不可比」之一，`evidence` 为非空字符串列表。「已验证」必须同时有合法 `baseline/comparison`，且 `task_mix=可比`，down 时复验值严格小于基线值，up 时严格大于；不满足则应保持「验证中」或改「已放弃」。覆盖可比才算已验证；趋势不证明因果。到期假设在 batch.md 显示基线 metric/direction/window/value，缺失时显示「无基线」。

## 主会话的评审方法

- 每个假设写清机制、能解释哪些记录、还应看到什么、看到什么就错；重要问题至少比较两个竞争解释。
- 不在 aiw 流程层的项目缺陷标为 `unexplained`，并用 `project_issues` 交回项目仓库，按仓库合并成一条或几条；主会话单次失误、已修复的问题不开 issue。
- 每批至少认真考虑一次「是我的规则或派单模板导致的」。
- 看比例，不看条数：分母是各分组的 answers 数。
- 只对支撑结论的关键记录派 codex-sol 只读核对，不逐条核对。
- 试修时填 `evidence`、`baseline`、`probe`、`expect_down`、`expect_flat`、`window_days`；窗口至少是原来发生间隔的 2–3 倍，未到窗口保持「验证中」。到期时跑探针，并按 rules_hash 确认两台都已同步了修复，记录可比窗口的 `comparison` 后再判断是否「已验证」。
- 允许本批结论是「证据不足，先补证」，不必每批都改东西。

## 已知限制

- 非 PRIVATE 仓库的项目缺陷只落在集中仓库，不会出现在项目仓库里；issues 关闭或查询失败也回落集中仓库；项目 issue 的实际目标为集中仓库时，搜索或创建前现查集中仓库可见性（同次 issues 调用只查一次，不信任当天缓存），非 PRIVATE 或查询失败则该条失败且不创建；任何情况下都不在非 PRIVATE 仓库创建 issue。

- issue 正文的出处无法自动判断：工具只保证 issue 指向的假设只解释自有记录，正文里写不写非自有仓库的细节靠主会话自律。
- issue 创建成功后、写入 `issues.json` 前进程崩溃，可能重复创建一个；另一台机器没有本机映射，靠「正文含完整 `aiw-key:` 行」的搜索兜底，搜索索引有延迟时也可能重复。发现后手动关闭即可。
- 记录写入位于派单的信号延迟区内（为了「写不成就不剥离」），最坏情况下 Ctrl-C 推迟十几秒到一分钟。

## 二期（未做）

项目会话启动时提示该项目的待办 issue；map 按批记录；座位采纳率；改动被覆盖、回滚的检测。
