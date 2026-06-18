# modi-harness v0.6 自查结果：Harness 与 Agent 边界

检查日期：2026-06-15

依据清单：`docs/reviews/selfcheck-v0.6.md`

验证命令：

```bash
uv run pytest
```

结果：592 passed，5 warnings。

## 一、总判断

当前项目已经具备 Harness 的基本形态，不是单纯的 Agent 封装。

核心执行入口集中在 `ModiSession` / `HarnessGraphAdapter` / LangGraph 节点中；`ModiAgent` 是声明式对象，没有 `run` 方法；模型调用、工具执行、上下文构造、输出校验和 trace 都由 Harness 侧模块统一处理。

但按 v0.6 清单衡量，当前仍未达到“强 Harness / token efficiency 工程化”的完整标准。主要缺口集中在：

- 缺少稳定的 `step_id`，目前主要使用 `step_count`。
- 模型 usage 已抽取，但没有写入 trace，也缺少 latency / cost 归因。
- 工具 spec 有 `timeout_seconds` / `retry` 字段，但 ToolGateway 执行路径未实际应用 timeout / retry。
- context 有 hash，但缺少 token 计量、裁剪记录、cache 命中记录和 tool result 压缩选择记录。
- 局部 repair 已有，但通用“失败类型 -> retry policy -> 局部重试”的运行时策略仍不完整。

结论：**部分通过，架构方向正确；可控性已起步，成本可解释性和工具运行治理仍是 v0.6 的主要短板。**

## 二、边界检查总表

| 检查项 | 结论 | 证据 | 风险 |
| --- | --- | --- | --- |
| 任务状态管理 | 部分通过 | state 初始化包含 `run_id`、`thread_id`、`task`、`status`、`step_count`、`repair_used`，并由 checkpointer 支持 resume | 无显式 `step_id`；失败节点定位主要依赖 trace/event，而非状态字段 |
| 工具执行控制 | 部分通过 | ToolGateway 统一执行 registry lookup、schema 校验、可见性、denied-retry、hook、policy、approval / preview | `timeout_seconds` / `retry` 字段未在执行时生效；工具调用次数限制依赖 graph `max_steps`，不是专门的 tool-call cap |
| 上下文构造 | 部分通过 | ContextManager 统一构建 `ContextPack`，包含 memory、references、state summary、tools、output contract、`context_hash` | 未记录 context token、裁剪内容、cache 命中、tool result 压缩选择 |
| 模型调用 | 通过 | `model_turn_node` 通过 `ModelAdapter.call(pack)` 调模型，Agent 不直接调用模型 | usage 未进入 trace/cost 汇总 |
| Prompt 模板 | 通过 | Agent instruction / skills 来自 `ModiAgent` / loader；Harness 核心只拼治理性系统消息和 output contract | builtins 对所有 Agent 可见，需持续确认不会变成业务 prompt 承载点 |
| 业务规则 | 通过 | 未发现 Harness 写死公安、投研、论文评分等业务规则 | output controller 有通用安全关键词规则，属于治理逻辑而非业务场景 |
| 输出 Schema | 通过 | `output_contract` 由 Agent 声明；`submit_output` 协议工具使用 schema；OutputController 强校验 JSON/schema/required/citation/risk/forbidden | 业务级校验规则仍偏弱，主要是通用规则 |
| Memory namespace | 部分通过 | Memory 有 user/workspace/agent/thread scopes，Session 生成 `MemoryScopeKeys`，model_turn 按 agent/thread 构造 scope | 缺少同一 run 内重复召回去重的明确记录 |
| Retry 策略 | 部分通过 | 模型 transient retry/fallback 已有；validation repair budget 已有；denied retry guard 已有 | 工具 retry 未实现；错误类型到 retry policy 的统一编排不足 |
| 成本统计 | 未通过 | `ModelUsage` 可从 LangChain message 抽取 prompt/completion/cache tokens | `model_result` trace 只记录 finish_reason；没有 usage、latency、cost_usd 归因 |
| 评测框架 | 通过 | pytest 覆盖 graph/runtime/tool/policy/context/model/output/subagent/smoke | 缺少专门面向 token/cost 回归的评测 |
| 评测用例 | 通过 | examples 和 tests 下已有多场景 case；Agent 可独立声明/加载 | 业务 Agent 的 eval case 机制尚未形成独立 package 约定 |

## 三、清单逐项结论

### 1. State

结论：**部分通过。**

已满足：

- `run_id`、`root_run_id`、`parent_run_id`、`thread_id`、`agent_name`、`task`、`status`、`step_count`、`repair_used` 在初始 state 中统一创建。
- `run()` / `resume()` 通过同一 graph 和 checkpointer 执行，支持中断后恢复。
- 工具调用记录包含 `tool_call_id`、tool name、args、decision、result/error、started/finished。

未满足：

- 没有清单要求的独立 `step_id`。
- 模型调用 trace payload 使用 `{"step": state["step_count"] + 1}`，但没有 step-level 输入/输出 ref。
- max step 达到后 route 直接结束，没有显式把状态标成 `failed` 或 `blocked`，可观测性不足。

### 2. Tool Gate

结论：**部分通过，边界正确但运行治理未满。**

已满足：

- Agent / Model 只能提出 tool proposal，实际执行统一进入 ToolGateway。
- 执行前有 registry lookup、agent visibility、JSON Schema 校验、denied-retry guard、hook、policy decision。
- 高风险工具支持 `require_approval` / `require_review` interrupt。
- preview / plan 支持 dry-run 或 simulated intercept。
- 工具失败会形成结构化 `ToolCallRecord.error`。

未满足：

- `timeout_seconds` 字段由 ToolRegistry 默认填充，但 ToolGateway 调用 handler 时没有 timeout 包裹。
- `retry` 字段存在，但未执行 retry policy。
- 没有独立的最大工具调用次数限制；目前由 `max_steps` 间接限制模型轮次。

### 3. Context Control

结论：**部分通过。**

已满足：

- Context 统一由 ContextManager 构建。
- `ContextPack` 记录 system / agent / skill / memory / references / state summary / visible tools / workspace index / output requirement / trust annotations。
- 有 `context_hash`。
- untrusted references 有边界包装和 trust annotation。
- memory 按 user/workspace/agent/thread scope 选择。

未满足：

- 没有 context token 统计。
- 没有记录哪些内容被裁剪。
- 没有 cache-aware context build 的命中/写入记录。
- tool result inline/offload 逻辑尚未完整落在 ContextManager 或 WorkspaceManager 选择层。

### 4. Validation

结论：**通过。**

已满足：

- Agent 声明 `output_contract`。
- 结构化输出通过 schema、required fields、citation、risk label、forbidden patterns 校验。
- 格式错误、字段错误、安全/side-effect 类错误有不同 code。
- validation 失败会把 issues 回灌给下一轮模型 repair。
- `repair_budget` 限制 repair 次数。
- validation 事件写入 trace。

待增强：

- 业务级 validator 目前主要靠通用 schema / required field；如需复杂业务规则，应把 Agent-provided validator/rule hook 纳入 contract。

### 5. Trace / Cost

结论：**Trace 部分通过，Cost 未通过。**

已满足：

- trace 事件有 run/root/parent/thread/timestamp/event_type/payload。
- trace middleware 写 JSONL，并通过 event_id 避免重复 flush。
- 记录 run_start、context_built、model_call、model_result、tool_result、approval、denial、output_validation、memory selection 等事件。

未满足：

- `model_name` / `provider` 未进入每次 model_call 或 model_result。
- `input_tokens` / `output_tokens` / `cache_read_tokens` / `cache_write_tokens` 已在 `ModelResult.usage` 可抽取，但没有写入 trace。
- `model_latency` / `tool_latency` 没有统计。
- `prompt_hash` 没有记录；只有 `context_hash`。
- `retry_count` 没有记录到 trace。
- `cost_usd` 当前恒为 `None`，没有归因。

## 四、关键边界判断题

| 问题 | 判断 | 说明 |
| --- | --- | --- |
| 新增 Agent 是否要改 Harness 核心代码？ | 基本不需要 | `ModiAgent` / markdown loader / session discovery 支持新增 Agent；除非新增 kernel tool 或 runtime 能力 |
| Agent 能否绕过 Harness 直接调用模型？ | 不能 | `ModiAgent` 无 run；执行入口为 `ModiSession.run_task`，模型调用在 graph node 内 |
| Agent 能否绕过 Harness 直接调用工具？ | 不能 | 工具 handler 绑定到 session registry，model 只能发 tool proposal |
| Harness 里有没有写死业务逻辑？ | 未发现明显业务污染 | 核心代码是治理、状态、工具、上下文、模型、输出、安全规则 |
| Agent 失败后谁决定重试？ | 部分是 Harness | validation repair、model transient retry 是 Harness；工具 retry 和通用错误类型策略仍缺 |

## 五、优先级建议

### P0：补齐 trace/cost 可解释性

- 在 `model_result` trace 中写入 `usage`：prompt/completion/total/cache read/cache write。
- 记录 `model_name`、`provider`、fallback provider、retry_count。
- 给 model call 和 tool call 增加 latency。
- 在 run_end 汇总 token/cost/latency。

### P1：实现 ToolSpec timeout/retry

- ToolGateway 按 `timeout_seconds` 包裹 handler。
- 按 `retry.max_attempts`、`retry_on`、`backoff_seconds` 执行工具局部 retry。
- trace 每次 attempt，失败时记录 normalized error code。

### P1：引入显式 step_id

- 每次模型调用和工具调用分配稳定 `step_id`。
- ToolCallRecord / TraceEvent payload / validation event 关联 step_id。
- max_steps 命中时写明确 terminal status 和 error。

### P2：增强 context governance

- 记录 context token 估算或 provider-returned prompt token 对应关系。
- 记录 recent messages 裁剪范围。
- 记录 memory 去重/重复召回抑制。
- 对大 tool result 做摘要/引用策略，并在 trace 中记录选择原因。

## 六、最终结论

modi-harness 当前已经满足“Agent 不负责运行时、Harness 负责统一治理”的基本边界。

它离 v0.6 清单里的理想 Harness 还差一层“运行可解释性”：尤其是 step-level trace、token/cost/latency 归因，以及工具 timeout/retry 的真实执行。

下一阶段最值得做的是：**先把每一步的 model/tool/validation 事件用 step_id 串起来，再把 usage/latency/cost 写进 trace。** 这样 Harness 的价值会从“能控”进一步变成“可解释、可优化、可回归”。
