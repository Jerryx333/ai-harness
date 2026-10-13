# 本机改进缓冲区

执行方看到的往往是小视角的症状。保留零散观察及机械信号，让主会话分批提出可证伪假设、评审和复验，避免凭一次报告就修改规则。所有记录和评审状态只存在本机 `$STATE/findings/`。`aiw gc` 保留整个目录。

## 修改边界

范围内可修须写 fixed（改动与应有行为）；范围外、规则/模板/工具只记不修；安全或数据破坏问题立即在正文报告。

## 配置

在配置的 findings 段设置：collect、hint 缺省 false；max_items=5、daily_cap=300、field_max=300、trigger_count=100、trigger_days=3。collect 决定追加记录块与解析；hint 决定钩子提示。数值必须是合法整数，daily_cap 可为零，其余须为正数。残留的已删除配置键静默忽略。
worker 收集不含会诊、map、无附件文本单；`--no-findings` 可跳过单次收集。记录失败只警告，不影响执行结果。

## 状态文件

| 文件 | 写入方 | 内容 |
| --- | --- | --- |
| `records.jsonl` | worker、`aiw note` | answers / observation / changes；flock 保护，只追加 |
| `quarantine.jsonl` | worker、note | 命中密钥特征的记录 |
| `records.lock` | record、triage | 记录追加与评审状态修改的 flock 互斥 |
| `daily-<YYYY-MM-DD>.count` | record | 当日非隔离观察计数，超 daily_cap 只计数 |
| `status.jsonl` | triage apply | 每行 {id, status, hypothesis, note, triage_id, ts} |
| `hypotheses.json` | triage apply | {id: 假设对象}，含 created / updated |
| `hypotheses-log.jsonl` | triage apply | 每行 {ts, triage_id, id, before, after} |
| `triages.jsonl` | triage apply | 每行 {ts, triage_id, host, records, hypotheses} |
| `count-cache.json` | record、triage apply | {open, last_triage, oldest_open} |
| `triage.lock` | triage start / apply / abort | {host, pid, triage_id, started, expires}，两小时租约 |
| `signals.json` | findings extract | 机械信号与 coverage 快照 |
| `triage/<id>/` | triage start | batch.md / batch.json / lock.json |
| `hint.json` | 钩子 | 上次提示时间，四小时节流 |

open 是 observation 中无处理状态或最新状态为 unexplained 的数量。record 成功追加观察后在记录锁内增量更新缓存；triage apply 在同一把锁内全量重算。quarantine、超限观察、answers、changes 不增加 open。last_triage 取最后一次 apply 时间，oldest_open 为最早开放观察时间。
钩子只读取小缓存及节流文件，不扫描记录日志。达到数量门槛，或有开放记录且距上次评审达到天数门槛（未评审过时看 oldest_open），显示「[缓冲区]」。收到提示，主会话开新会话运行 `aiw triage start`。

## 记录格式

worker 在 prompt 末尾追加：
```text
<<<AIW-FINDINGS
guess: 猜测，无则无
deviation: 偏离
abandoned: 放弃
fixed: 改动 / 应有行为
- observation: 现象 | expected: 应怎样及依据 | evidence: 命令输出或文件:行 | location: 路径:函数
AIW-FINDINGS>>>
```

parse 保留合法记录块前的回答，坏块不剥离；少量尾随正文保留并提示。answers 保存前四字段，observation 每项单独一行，changes 保存改动文件。文本去掉代码围栏、折叠空白、按 field_max 截断。自由文本密钥扫描命中时写 quarantine.jsonl；普通观察超过 daily_cap 只增加日计数。
公共字段包含 id、ts、host、session、source、type、seat、actual、via、ro、prompt_id、run_dir、repo、repo_rel、workdir、commit、branch、rules_hash、effective_hash、partial_context。仓库从 origin 解析；没有可解析 origin 时保留本机 workdir。

`aiw note <workdir> <现象> [--expected 文本] [--evidence 文本] [--location 文本]` 使用同样的清洗、扫描和追加规则，source=main。`aiw findings status` 展示本机计数。

## 机械抽取

`aiw findings extract [--days N]` 抽取 overlap（24 小时内不同 run_dir 修改同文件）、rule_churn（规则同节至少三次提交）、repeated_instruction（至少五份不同任务出现的长指令行）。来源只有本机 records、规则 Git 历史、runs；自动追加提示不计入重复指令。
signals.json 的 coverage 为 {sources, level, reasons}；sources 每项含 name、exists、rows、bad，输入计数在信号过滤和去重之前。level 为「无数据/部分/完整」，reasons 说明缺失、坏行或跳过。覆盖只说明读到多少，不代表结论可信度。

## 评审流程

1. `aiw triage start [--days N]` 获取本机 triage.lock，活跃租约拒绝，过期租约可接管；生成 batch.json 与 batch.md。pending 是无状态的 observation；pool 是 days 窗口内状态为 unexplained / unverifiable 的 observation。
2. 主会话核对关键记录和原文，先考虑自己的规则与派单是否造成症状，填写 decisions.json。batch 展示概况、假设台账、到期探针和基线、机械信号、分组、记录与模板；已验证/已放弃假设不展示。
3. `aiw triage apply <decisions.json>` 校验锁归属、决定和证据；追加处理状态、假设变更日志与评审日志，更新假设快照和计数缓存，然后释放锁。校验失败保留锁。
4. `aiw triage abort` 仅释放属于本批的锁。嵌套执行方不得调用 triage。

## decisions.json 结构和校验

```json
{
  "self_cause_considered": "考虑了自身规则和派单",
  "hypotheses": [{
    "id": "H-20261013-1", "title": "超时原因", "mechanism": "机制描述",
    "explains": ["记录id"], "predicts": ["预测"], "refuted_by": ["反例"],
    "competing": [], "status": "验证中", "layer": "机制",
    "evidence": {"kind": "已见后果", "refs": ["tests:1"]},
    "baseline": {"metric": "每100单超时数", "direction": "down", "window": "修前窗口", "value": 10, "evidence": ["before.json"]},
    "comparison": {"window": "修后窗口", "value": 5, "task_mix": "可比", "evidence": ["after.json"]},
    "probe": "复验命令", "expect_down": ["超时"], "expect_flat": [], "window_days": 7
  }],
  "records": [{"id": "记录id", "status": "explained", "hypothesis": "H-20261013-1", "note": "证据"}]
}
```

self_cause_considered 非空；hypotheses / records 是对象列表。假设 id、title、mechanism 非空；explains、predicts、refuted_by 为非空字符串列表，explains 引用本批 records。layer 是机制/项目规则/代码；假设状态是待补证/支持/反驳/试修/验证中/已验证/已放弃。records 的 id 在 pending 或 pool 中，status 是 explained/unexplained/stale/wontfix/unverifiable；explained 或非空 hypothesis 必须引用本次提交或已有假设。
evidence.kind 是已见后果/明文要求/确定缺陷/线索，refs 是非空字符串列表，仅线索允许空 refs。支持/试修/验证中/已验证必须有非线索证据；其余状态可省略，提供则校验。
试修/验证中/已验证必须有 baseline：非空 metric/window/evidence，direction=down/up，有限数字 value（不能是 bool）。试修/验证中还须有非空 probe、expect_down 与正整数 window_days。
已验证必须有 comparison：非空 window/evidence、有限数字 value、task_mix=可比/未核实/不可比；最终必须 task_mix=可比且按 direction 严格改进，否则保持验证中或改已放弃。其余状态可省略测量字段，提供则校验。
假设字段（已验证的 explains 引用除外）和状态 note / hypothesis 扫描密钥，命中拒绝。主会话手动评审、决定后续修改，并以基线与可比任务窗口复验。
