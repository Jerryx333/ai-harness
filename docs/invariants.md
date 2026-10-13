# 红线与守护测试

运行 `python3 tests/mutation_check.py` 检查全部红线，或追加表内变异 id 只检查指定项；仅使用临时副本，先验证基线再逐条变异。修改红线相关代码之前和之后都必须运行，并运行 `python3 tests/test_harness.py` 完整回归。出现“补丁失效”时，核对实现后更新 `MUTATIONS` 的精确 old/new（old 必须唯一），保持只破坏对应红线，并同步本表及守护测试，不能把语法错误当作杀死。变异必须由预期的那条断言杀死才算数；无关失败不算通过。

| 不变量 | 代码位置（文件:函数） | 守护测试用例 | 变异 id |
| --- | --- | --- | --- |
| 凭据类附件拒发 | `ai_worker.py:sensitive` | `attach` | `credentials` |
| 附件不出 workdir | `ai_worker.py:build_attachments` | `attach` | `workdir` |
| AIW_NESTED 不再派单 | `ai_worker.py:dispatch` | `nested_dispatch` | `nested` |
| 写任务不顶替给只出文本的座位 | `ai_worker.py:dispatch` | `write_fallback_text_seat` | `write-fallback` |
| 只出文本的座位不接写任务 | `ai_worker.py:dispatch` | `worker_regression` | `text-write` |
| Codex 专属行为按 adapter 而非座位名启用 | `ai_worker.py:codex_workspace_write` | `codex_adapter_alias` | `codex-adapter-name` |
| codex-sol 不顶替 | `seats.json:fallback` | `codex_sol_no_fallback` | `sol-fallback` |
| 超时不换座位 | `ai_worker.py:dispatch` | `worker_regression` | `timeout` |
| rc=0 且有输出优先判 ok，回答谈 quota/401 不熔断 | `ai_worker.py:classify` | `worker_regression` | `success-first` |
| 工具调用伪答复必须识别 | `ai_worker.py:looks_like_tool_calls` | `tool_call_detection` | `tool-calls` |
| goal verify 命令防篡改 | `goal.py:_verify` | `goal_verify_integrity` | `verify-sha` |
| goal 保险丝在执行前判定 | `goal.py:_verify` | `goal_fuse_preflight` | `goal-fuse` |
| 会诊不顶替 | `panel.py:ask` | `panel` | `panel-fallback` |
| 会诊至少两家成功且有产出，单家不能降低门槛 | `panel.py:main` | `panel_opinion_threshold` | `panel-opinion-threshold` |
| 写任务零变化必须提示 | `ai_worker.py:WorkChanges.__exit__` | `write_changes_report` | `write-zero` |
| CLI output.md 必须落盘 | `ai_worker.py:run_seat` | `run_dir_persist` | `output-file` |
| Codex 信任检查不能恒为通过 | `doctor_config.py:trust` | `doctor_config` | `codex-trust` |
| 运行中库锁识别为 locked，不熔断、不顶替 | `ai_worker.py:classify` | `codex_locked_runtime` | `locked` |
| inflight 字段符合状态文件约定 | `ai_worker.py:dispatch` | `test_state_contract` | `inflight-contract` |
| worker 持锁表明在途且锁不被子进程继承 | `ai_worker.py:acquire_liveness_lock` | `inflight_liveness_lock` | `inflight-lock` |
| 拿锁失败不留下假锁文件 | `ai_worker.py:acquire_liveness_lock` | `inflight_liveness_lock` | `inflight-lock-cleanup` |
| 子进程创建登记完成后才交付中断 | `ai_worker.py:run_group` | `signal_startup_window` | `startup-deferred` |
| aiw 强杀后看门狗清理独立执行方进程组 | `ai_worker.py:WATCHDOG_CODE` / `run_group` | `executor_watchdog` | `executor-watchdog` |
| 延迟信号不遮蔽关键区或 finally 清理区正在传播的真实异常，保留待处理信号；切换恢复嵌套深度与屏蔽字，首次信号决定退出码 | `ai_worker.py:deferred_signals` / `run_group` / `interruptible_signals` / `interrupted` | `signal_windows` | `deferred-exception` |
| finally 清理区不交付正在传播真实异常时的待处理信号 | `ai_worker.py:run_group` | `signal_windows` | `cleanup-deferred-exception` |
| 停用座位不能派单 | `ai_worker.py:dispatch` | `free_guard` | `disabled-dispatch` |
| agent-use transient 失败顶替且不熔断；未知 reason 不自动改派 | `ai_worker.py:dispatch` / `AGENT_USE_REASON` | `agent_use_transient` | `transient-fallback` |
| 已存在的 errand 名单必须严格校验 | `ai_worker.py:errand_names` | `errand_registry_corrupt_rejected` | `errand-registry` |
| errand 不进入 panel | `panel.py:main` | `errand_panel_rejects` | `errand-panel` |
| errand 不进入运行时 route | `route.py:route` | `errand_route_runtime_rejects` | `errand-route` |
| freecheck 不探测 errand | `freecheck.py:check` | `errand_freecheck_skips` | `errand-freecheck` |
| 支持及试修验证必须有非线索证据 | `triage.py:validate` | `triage_validate_core` | `triage-evidence` |
| 已验证必须有可比且改进的基线复验 | `triage.py:validate` | `triage_baseline_verified` | `triage-baseline` |
| unexplained 观察仍计入 open | `findings.py:recompute_count_cache` | `findings_local_cache_hint` | `findings-open` |

跑腿只能点名 `aiw errand`，不进入文本座位和顶替链；嵌套执行方不得调用。提交前说明会花额度，账户及私人数据仅限用户同意的该单。提交、动作放行、回复提问必须由用户亲手点击 macOS 弹窗；禁止 System Events、辅助功能、脚本代点或改代码绕过。派单记录包含 job_id、state、task_url、credits_used。弹窗及额度逻辑由 agent-use 实现，本仓库离线测试只验证转发和隔离。
