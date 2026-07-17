# Research Assistant 技术架构

本文描述 `research-assistant` 当前版本的核心方案、执行边界和扩展方式。它是实现基线，不记录历史迁移过程，也不承诺尚未实现的数据源、知识库、图谱或通用研究平台能力。

## 1. 目标与边界

Research Assistant 解决的是公开资料研究，而不是通用聊天或通用工具执行。当前版本覆盖三类请求：

- 明确、狭窄、一次检索通常能够回答的查询；
- 需要比较、评估、尽调或多问题综合的深度研究；
- 天气、翻译、编码、提醒和网页操作等非研究请求的明确拒绝。

系统追求的不是让模型自由生成任意流程，而是把稳定控制权和局部自主能力分开：

> Workflow 管理稳定业务主航道，AgentLoop 只在一个 Autonomous Node 内自主解决问题。

当前明确不做私域知识库、实体图谱、付费搜索 API、动态子图编译、并行 Workflow Node、跨 Workflow 跳转和多层嵌套 Workflow。

## 2. 核心设计原则

### 2.1 单一 Agent，多个 Workflow

`research-assistant` 是一个 Agent，不是三个独立 Agent。它声明三个 Workflow，由 Router 根据用户请求选择唯一入口。Agent 共享同一组指令、Skill、Operation 和权限边界，Workflow 决定具体执行路径。

### 2.2 两种 Node 执行方式

- `operation`：Runtime 已经知道要执行哪个可信 Operation；模型不参与操作选择。
- `autonomous`：Workflow 只规定目标、输入、能力和完成契约；AgentLoop 在节点内部逐步规划和执行。

模型可以选择当前自主节点内的下一步，但不能修改 Workflow、节点目标、工具范围、Schema、预算或后续迁移。

### 2.3 搜索不等于研究完成

`public_web_search` 只产生候选来源和可用网页内容。候选证据必须先经过 `verify_claim_evidence` 逐条标注 `supporting`/`contradicting`/`unrelated`、`independent`/`same_origin`、`direct`/`indirect`，才能进入 `record_research_finding`。只有 `record_research_finding` 能关闭一个研究问题，并把结论、影响、置信度和逐项标注证据绑定到对应 `task_id`。

这条边界防止“调用过搜索工具”被误判成“问题已经解决”，也防止“看到了几条候选网页”被误判成“证据已经核实”。

### 2.4 模型提出完成，Harness 决定完成

模型通过 `complete_node` 提交候选结果。Harness 负责：

1. 校验 Node 输出 Schema；
2. 检查必填字段是否有意义；
3. 确认 TaskPlan 没有开放问题；
4. 验证引用确实来自当前问题观察到的可用来源；
5. 从已记录 Finding 自动组装最终关键发现、引用和置信度（置信度由 Harness 侧的 Operation 依据证据计算，模型不得自行提供）；
6. 写入 Workflow State 并执行稳定迁移；
7. 深度研究完成后再经过一个确定性 `finalize_report` 节点，从已组装的关键发现生成 Mermaid 证据图谱。

模型不能绕过这些检查。

## 3. 总体架构

```mermaid
flowchart TD
    U[用户请求] --> D[Agent Discovery]
    D --> A[Research Assistant]
    A --> R[Workflow Router]

    R --> Q[quick_lookup]
    R --> X[deep_research]
    R --> J[reject_unsupported]

    Q --> QT[operation: get_current_time]
    QT --> Q1[operation: public_web_research]
    Q1 --> Q2[autonomous: answer]

    X --> X1[autonomous: confirm_scope]
    X1 --> H[Harness Node Review]
    H --> X2[autonomous: investigate]
    X2 --> P[TaskPlan]
    P --> N[get_current_time]
    N --> S[structured public_web_search]
    S --> V[verify_claim_evidence]
    V --> F[record_research_finding]
    F --> P
    P --> C[complete_node]
    C --> Z[operation: finalize_report / build_evidence_graph]

    J --> J1[operation: reject_research_request]

    Q2 --> WR[Workflow Runtime]
    Z --> WR
    J1 --> WR
    WR --> O[最终输出]
    WR --> T[State / StepRecord / Trace / Checkpoint]
```

组件职责如下：

| 组件 | 负责 | 不负责 |
| --- | --- | --- |
| `agent.toml` | 声明 Agent factory，供 CLI 和 Discovery 定位 | Workflow 和运行逻辑 |
| `agent.py` | 组装 Agent、Workflow、Skill、Operation 和权限 | 节点调度 |
| Router | 选择一个已声明 Workflow，并构造合法输入 | 回答用户、调用研究工具 |
| Workflow Runtime | 执行 Node、迁移、预算、校验和持久化 | Operation 内部搜索算法 |
| AgentLoop / Brain | 在一个 Autonomous Node 内决定下一步 | 修改主 Workflow |
| Research Operations | 搜索、抓取、记录 Finding 或拒绝请求 | 决定跨节点流程 |
| CLI Renderer | 展示必要交互和进度 | 作为事实日志 |
| Trace | 记录完整执行证据 | 作为 Agent Memory |

## 4. Agent 定义与发现

### 4.1 `agent.toml`

```toml
factory = "agent:build_agent"
```

它告诉 Discovery：从当前 Agent 包的 `agent.py` 导入 `build_agent()`。CLI 执行 `modi research-assistant` 时，首先通过这个声明找到 Agent。

### 4.2 `agent.py`

`build_agent()` 是组合根，负责构建完整 `ModiAgent`：

- 名称、描述和统一 Agent 指令；
- `query-planning` 与 `web-research` 两个 Skill；
- 三个 YAML Workflow；
- 七个可信 Operation（`get_current_time`、`public_web_research`、`public_web_search`、`verify_claim_evidence`、`record_research_finding`、`build_evidence_graph`、`reject_research_request`）；
- Permission Profile。

Workflow YAML 在加载时即校验：引用的 Operation 必须属于 Agent，输入和完成 Schema 必须合法。运行时不会临时发现未知工具。

## 5. Router 与三个 Workflow

当调用方未指定 `workflow_id` 且 Agent 声明多个 Workflow 时，模型 Router 只能调用一个形如 `route__<workflow_id>` 的路由工具。每个工具的描述和输入 Schema 直接来自对应 Workflow。Router 的输出必须满足：

- 恰好选择一个已声明 Workflow；
- 参数是对象；
- 参数通过选中 Workflow 的 `input_schema`。

Router 不回答问题，也看不到研究 Operation。

| Workflow | 适用请求 | 主路径 |
| --- | --- | --- |
| `quick_lookup` | 明确实体或窄问题，一次检索通常足够 | Current Time → Search Operation → Autonomous Answer |
| `deep_research` | 比较、评估、尽调、技术实力、风险和多维综合 | Scope → Review → TaskPlan Investigation |
| `reject_unsupported` | 非公开资料研究任务 | Deterministic Reject Operation |

调用方也可以显式传入 `workflow_id`。Checkpoint resume 始终沿用首次选中的 Workflow，不能在恢复时换路。

## 6. Quick Lookup

```mermaid
flowchart LR
    I[subject + question] --> T[operation: get_current_time]
    T --> S[operation: public_web_research]
    S --> A[autonomous: answer]
    A --> C[complete_node]
    C --> E[$complete]
```

`current_time` 与 `search` 都是确定性 Operation Node。`current_time` 先生成本次运行内、短时有效且只能使用一次的 `time_token`，`search` 再携带该 token 执行一次 `public_web_research`。搜索 Operation 做严格实体检索、候选排序和少量页面抓取。

`answer` 是无工具 Autonomous Node。它只能依据上一步的 `research_result` 生成：

- `executive_summary`；
- `citations`；
- 可选 `limitations`。

这种拆分让检索行为稳定，同时保留模型对检索结果的自然语言归纳能力。

## 7. Deep Research

### 7.1 Scope 确认

`confirm_scope` 是无工具 Autonomous Node。它把用户请求转换为：

```yaml
subject: 研究主体
research_question: 用户真正需要判断的问题
decision_context: 可选, 用户要据此做什么决定
constraints: 可选, 范围或口径约束
task_plan:
  items:
    - id: stable_task_id
      title: 可独立关闭的研究问题
```

TaskPlan 必须包含 2–4 个互不重叠、能够直接支撑最终判断的问题。`completion.review: required` 表示模型提交草案后，Harness 创建 Node Review Interaction，由用户选择开始、修改或取消。

确认是 Workflow 控制行为，不由模型额外调用一次 `request_user_input`。

TaskPlan item 在这一步只有 `id`/`title` 两个字段可用——`completion.review: required` 的节点，其审阅回写路径会用 Harness 通用的 `TaskItem`（固定 `id`/`title`/`status`/`summary` 四字段）重建每一项，任何额外字段都会在校验前被丢弃。因此每个问题的 `verification_method`（验证方式）不在这一步声明，而是延后到 7.2 节所述的 `investigate` 阶段、模型开始处理该问题时才现场判断。

### 7.2 Investigation 循环

```mermaid
sequenceDiagram
    participant B as Brain
    participant R as Workflow Runtime
    participant T as get_current_time
    participant S as public_web_search
    participant V as verify_claim_evidence
    participant F as record_research_finding
    participant H as completion Harness

    loop 每个 TaskPlan 问题
        B->>B: 现场判断该问题的 verification_method
        alt verification_method = unverifiable_flag
            B->>F: 直接记录 blocked Finding, 不搜索
        else 其余四种方法
            B->>T: 读取当前时间并取得一次性 time_token
            B->>R: 按实体规划 structured searches
            R->>S: 携带 time_token 执行搜索
            S-->>R: search_id + records + usable sources
            B->>V: 携带全部 search_id, 逐条标注全部 usable URL
            V-->>B: verification_id + verified evidence
            B->>F: 携带最新 verification_id 记录 Finding
        end
        F-->>R: 关闭当前 TaskPlan item, Harness 计算 confidence
    end

    B->>H: complete_node(direct_answer, limitations)
    H->>H: 组装 Finding、校验引用与覆盖率
    H-->>R: Node completed
```

每个 TaskPlan 问题在开始处理时，由模型自行判断合适的 `verification_method`（不是在 `confirm_scope` 阶段声明的，见 7.1 节）：

| `verification_method` | 含义 |
| --- | --- |
| `single_source_sufficient` | 一条官方/一手来源即可关闭 |
| `dual_independent_required` | 需要至少两条相互独立的印证来源 |
| `official_primary_required` | 媒体/二手来源不能关闭，必须官方或一手 |
| `contradiction_sensitive` | 必须主动搜索反面证据 |
| `unverifiable_flag` | 判定为公开搜索无法核实，直接短路 |

`unverifiable_flag` 的问题不得调用任何搜索工具，直接以 `blocked` 状态记录并说明原因；一旦该问题已经搜索，Runtime 也不允许再切换到 `unverifiable_flag`。其余四种方法都要求严格执行 `get_current_time → public_web_search → verify_claim_evidence → record_research_finding`。`public_web_search` 每个问题最多调用两次（一次基础查询 + 一次针对性补搜），每次补搜前必须重新取时。一次搜索调用可包含 1–2 个实体项，provider 请求在 Operation 内部并行，Workflow 本身没有并行 Node。

搜索得到候选来源后，必须调用 `verify_claim_evidence`，传入该 `task_id` 在本次运行中产生的全部当前 `search_id`，并对这些搜索返回的每一个 usable URL 逐条标注 `stance`（`supporting`/`contradicting`/`unrelated`）、`independence`（`independent`/`same_origin`）和 `directness`（`direct`/`indirect`）。即使来源最终被判为 `unrelated`，也必须进入评估集合。只有所有选中搜索都没有 usable URL 时，空 `items` 验证才合法。

来源出处不再依赖进程内全局缓存。Runtime 直接从同一 `run_id` 的持久化 `StepRecord` 和 Operation 输出重建 `search_id → usable URL` 集合。如果补搜产生了新 `search_id`，此前的 `verification_id` 立即失效，必须重新覆盖首轮与补搜的完整来源集合。Finding 只需引用最新 `verification_id`；Runtime 会把该验证输出中的 evidence 原样注入 Finding，模型不再手工复制 claim、URL、来源类型、立场、独立性、直接性和日期字段。如果两条被标注为 `independent` 的证据共享域名，Operation 同样会拒绝并要求修正。

Finding 有两种状态：

- `sourced`：有来自当前问题可用来源、且已通过 `verify_claim_evidence` 标注的证据；
- `blocked`：`unverifiable_flag` 问题，或有限公开搜索无法形成可靠结论。

`blocked` 不会让整个研究立即失败。Runtime 将该问题关闭为 `[limited]`，继续研究后续问题，并在最终结果中生成明确限制。用户可以在最终报告后提供新线索，再发起增量研究。

`record_research_finding` 调用时必须带上该问题选定的 `verification_method` 和已标注证据，但**不得**提供 `confidence`——置信度完全由 Harness 侧的 Operation 依据六个离散因子计算，见第 9 节。

### 7.3 预算与唯一收尾步骤

`investigate` 的配置上限固定为 40 步。Runtime 仍保留按 TaskPlan 数量扩展的通用下限：

```text
max(40, task_count × 4 + 4)
```

每个正常关闭的问题至少消耗 4 步（`get_current_time` → `public_web_search` → `verify_claim_evidence` → `record_research_finding`），补搜还会再消耗取时、搜索和重新验证步骤。由于范围固定为 2–4 个问题，40 步能覆盖四项研究、一次定向补搜和若干协议修正；`unverifiable_flag` 问题只消耗 1 步。

如果最后一个 Finding 恰好在上限步骤关闭计划，Runtime 只额外保留一个 completion step。此时所有普通工具从 Brain 的可用能力中移除，只允许综合已有 Finding 并调用 `complete_node`。

这避免 `4/4` 后出现 `max_auto_steps_reached`，同时不会把开放计划变成无限续步。

## 8. 公网搜索 Operation

当前搜索不依赖付费 API 或 API Key，而是并行访问三个公开入口：

- Bing RSS；
- 百度 HTML 搜索；
- DuckDuckGo HTML，失败时回退到 DuckDuckGo Lite。

这意味着当前方案没有搜索 API 费用，但公开网页不是稳定的官方自动化接口，可能出现限流、验证码、页面结构变化或服务条款限制。生产环境若要求稳定 SLA，应替换为正式授权的搜索 API；这一替换只影响 Operation 实现，不改变 Workflow 协议。

### 8.1 有界检索

| 限制 | 当前值 |
| --- | --- |
| 每个 deep research 搜索批次的实体项 | 1–2 |
| 每个 provider 返回的候选数 | 最多 4 |
| 候选页面抓取尝试 | 最多 5 |
| 最终可用来源 | 最多 3 |
| `public_web_search` 每个 task 的调用次数 | 最多 2 |
| `verify_claim_evidence` 调用次数 | 不设专门预算，受 TaskPlan 规模和 Node 步数上限自然约束 |

搜索响应将 provider 状态区分为 `ok`、`empty`、`blocked` 和 `failed`。只有 `ok` 与 `empty` 表示 provider 给出了健康响应；“没有结果”和“服务不可用”不能混为一谈。

### 8.2 当前时间与一次性 token

每次公网搜索前必须调用 `get_current_time`。它同时返回 UTC、`Asia/Shanghai` 本地时间、当前日期/年份，以及随机 `time_token`。搜索 Operation 声明通用 `fresh_output_prerequisite` 元数据，Runtime 据此强制：

- token 必须来自同一 Workflow run 中此前成功完成的 `get_current_time`；
- 有效期为 120 秒；
- 每个 token 只能授权一次搜索调用；
- 已持久化的搜索 InvocationRecord 会消耗 token，即使 provider 最终失败；
- 重启后仍从 InvocationRecord 重建签发和消费状态，不依赖进程内字典。

缺失、未知、跨 run、过期或复用 token 都会在 provider dispatch 之前被拒绝，并提示 Brain 重新取时。Trace 的 `operation_summary` 记录本次取时的时间和时区，但不记录 token；搜索摘要记录 `search_id`、结构化查询、provider 健康度、每个实体候选数和可用来源 URL，不包含网页正文摘录。

在 Autonomous Node 中，取时成功后，Planner 会把刚签发的 token 作为显式 `fresh_output_prerequisites` 放到下一步上下文，并暂时从可选工具中隐藏 `get_current_time`，只保留对应搜索工具。这样模型无需从不断增长的历史步骤中寻找 token，也不能在真正搜索前无意义地重复取时；搜索完成或 token 未成功签发后，这个临时约束自然消失。

### 8.3 两种搜索语义

- `public_web_research`：面向明确主体，强调实体名称匹配，用于 Quick Lookup。
- `public_web_search`：面向一个研究问题，接收结构化实体查询，用于 Deep Research。

`public_web_search` 不再接受扁平 `queries: string[]`，也不保留“字符串恰好是 URL 就直接抓取”的旁路。每个 `searches` item 必须声明 `query`、`entity`、`dimension` 和可选 `aliases`。比较任务把双方放在同一次调用的两个 item 中，例如 `Tesla Model Y` 与 `小米 YU7` 各占一个 item。

实体身份采用去除空格和标点后的完整短语匹配：`Tesla Model Y`、`Tesla ModelY`、`Tesla Model-Y` 都归一为 `teslamodely`，`小米 YU7` 与 `小米YU7` 都归一为 `小米yu7`。`Y` 不会作为独立身份 token 获得加分，因此 Model 3 或泛 Tesla 页面不能冒充 Model Y。

候选先在每个实体池内独立排序，再轮询分配最多 5 个抓取槽位；两个非空实体池都会在任一实体获得第二个槽位前先获得一次尝试。URL 去重遇到共享候选时会继续寻找该实体的下一个候选，不会让另一个实体失去覆盖。

## 9. Evidence Ledger 与离散置信度

Research Assistant 没有单独引入新的 Evidence Graph 数据库对象。证据账本由现有运行记录组成：

```text
RuntimeOperation output
  + StepRecord
  + TaskPlan item
  + recorded Finding (含 verify_claim_evidence 标注结果)
  = 可审计的研究证据链
```

`record_research_finding` 记录：

- `task_id` 与研究问题；
- 直接结论；
- 对用户判断的意义；
- 该问题选定的 `verification_method`；
- `high` / `medium` / `low` 置信度（Harness 计算，模型不得提供）；
- `sourced` / `blocked` 状态；
- claim-level evidence（每条含 `stance`、`independence`、`directness`）；
- 限制条件。

每条证据包含 `claim`、`source_url`、`source_type`、`stance`（`supporting`/`contradicting`）、`independence`（`independent`/`same_origin`）、`directness`（`direct`/`indirect`）和可选 `as_of`。来源类型包括官方、一手来源、可信媒体、行业报告、招聘样本和二手来源。

### 9.1 出处与独立性复核

模型标注证据 `independent` 或 `same_origin`，但独立性不是自称的：`verify_claim_evidence` 会按域名复核，如果两条被标为 `independent` 的证据实际共享同一域名，Operation 拒绝该调用，要求模型改标注或换证据。`source_url` 是否真的来自当前问题也由 Runtime 根据持久化 search output 校验：验证调用必须引用当前 task 的全部 `search_id`，评估 URL 集合必须精确等于这些搜索的 usable URL 并集，拒绝模型凭记忆引用、跨问题挪用或漏看不利来源。

### 9.2 六因子离散置信度

置信度不是模型直接给出的数字或档位，而是 `agents/research_assistant/confidence.py` 依据六个各自离散为 `high`/`medium`/`low` 的因子计算：

| 因子 | High | Medium | Low |
| --- | --- | --- | --- |
| source_quality | 官方 / 一手来源 | 可信媒体 / 行业报告 | 招聘样本 / 二手来源 |
| source_independence | ≥2 条独立来源（复核后） | 1 条独立 + 其余同源 | 全部同源 / 单一来源 |
| directness | 直接支持结论 | 需要推断 | 仅间接相关 |
| recency | `as_of` 在 90 天内 | 在 365 天内 | 更早或缺失 `as_of` |
| consistency | 无反面证据 | 有反面证据但被压倒 | 反面证据 ≥ 支持证据 |
| coverage | 完全满足该 `verification_method` 要求的证据形态 | 部分满足 | 未满足 |

组合规则：**总体置信度 = 六个因子中最低的一个**（`low < medium < high` 的 `min()`）。任何一个因子差都会拉低整体结论，不允许用其余五个因子的高分抵消。当 `coverage` 因子未达到 `high` 时，Harness 还会自动向 `limitations` 追加一条说明该 `verification_method` 具体缺什么证据的文字。

### 9.3 Runtime 强制的不变量

Runtime 强制以下不变量：

1. Finding 引用必须来自同一 `task_id` 已观察到的可用来源；
2. 验证必须覆盖该 task 当前全部 `search_id` 和全部 usable URL；
3. 补搜后旧 `verification_id` 失效，Finding 只能引用最新完整验证；
4. Finding evidence 必须与验证输出逐字段一致；
5. 每个已解决问题必须存在对应 Finding；
6. 最终关键发现必须与记录的 Finding 内容一致；
7. 最终引用必须精确等于各 Finding 实际使用来源的并集；
8. Limited 问题不能伪造引用，并保留 blocked Finding 的实际结论与限制；
9. `unverifiable_flag` 问题必须以 `blocked` 状态记录，且只允许在未搜索时使用。

因此，Brain 负责判断和表达（选择 `verification_method`、标注证据关系），Harness 负责证据归属、独立性复核、置信度计算和完整性。

## 10. `complete_node` 与最终结果组装

Deep Research 的模型在 `investigate` 节点最终只提交：

```yaml
direct_answer: 对用户问题的直接综合回答
limitations:
  - 总体限制
```

Harness 从已记录 Finding 自动加入：

```yaml
key_findings:
  - task_id
    question
    conclusion
    implications
    verification_method
    confidence
    status
    evidence
citations:
  - https://...
```

这避免模型在收尾时重新抄写 Finding，导致结论、引用或置信度漂移。CLI 再将引用编号放到对应 evidence claim 旁边，而不是只在末尾输出无关联 URL 列表。

### 10.1 `finalize_report`：证据图谱

`investigate` 完成后，`deep_research` 还有一个确定性 `operation` Node —— `finalize_report`，执行 `build_evidence_graph` Operation：

```yaml
- id: finalize_report
  execution: operation
  operation: build_evidence_graph
  inputs:
    report:
      $ref: "#/nodes/investigate/output"
  transitions:
    completed: $complete
    failed: $fail
```

它是纯函数：读取已组装的 `key_findings`，为每个问题和每条不重复的证据来源各生成一个 Mermaid 节点，`supporting` 关系画实线、`contradicting` 画虚线，`sourced`/`limited` 问题分别用不同节点样式区分，输出附加一个 `evidence_graph` 字段（`flowchart LR` 文本）。因为是 `operation` Node 而不是 `autonomous`，它不经过 TaskPlan-completion 校验器，只对已经校验过的数据做确定性变换。

`evidence_graph` 不进入 CLI 的实时 Live 渲染——CLI 保持不变，证据图谱只出现在最终汇总结果里，供下游消费（例如导出报告、前端渲染）使用。

## 11. 状态、Checkpoint 与 Trace

Workflow State 持久化当前节点、节点尝试、Node 输出、迁移、AgentLoop 状态、StepRecord、TaskPlan、Pending Operation 和人工输入。恢复运行时会检查：

- Workflow ID 未变化；
- Workflow definition fingerprint 未变化；
- Execution Contract fingerprint 未变化。

Trace 记录完整执行事实，包括 Workflow 选择、Node、Operation、Step、completion rejection、interaction、TaskPlan 更新和 terminal 状态。CLI 只展示用户需要理解的子集。

```python
response = session.run_task(
    agent="research-assistant",
    input={"prompt": "对比零跑和理想汽车"},
    thread_id="auto-compare-001",
)

for event in session.get_trace("auto-compare-001"):
    print(event["event_type"], event["payload"])
```

Trace 是审计和调试证据，不是 Agent Memory，也不会自动进入下一次模型上下文。

## 12. CLI 交互模型

Deep Research CLI 有意隐藏内部噪声，只展示：

1. 一个 `Research scope` 框；
2. 框下方的确认输入；
3. 确认后由同一个框原位更新研究问题状态；
4. 最终报告。

Scope 输入期间使用静态 Panel，避免 Rich Live 与终端输入争夺光标。用户确认后，Renderer 清除静态预览并由唯一 Live 原位接管。Spinner 只存在于执行阶段。

任务标记语义：

- `○`：待处理；
- `●`：研究中；
- `✓`：有证据完成；
- `△`：有限证据完成，最终保留限制。

Operation 名称、查询词、模型 narration 和 completion repair 仍在 Trace 中，但不作为用户进度重复打印。

## 13. 执行方式

### 13.1 CLI

```bash
modi research-assistant
```

### 13.2 Python API

```python
response = session.run_task(
    agent="research-assistant",
    input={"prompt": "威灿科技"},
    thread_id="company-lookup-001",
)
```

需要确定性选择 Workflow 时可以显式指定：

```python
response = session.run_task(
    agent="research-assistant",
    workflow_id="quick_lookup",
    input={
        "subject": "威灿科技",
        "question": "威灿科技的主营业务是什么？",
    },
    thread_id="company-lookup-002",
)
```

Node Review 返回 waiting 后，通过同一个 `thread_id` 恢复：

```python
response = session.respond_to_interaction(
    thread_id="auto-compare-001",
    interaction_id=interaction_id,
    decision="approved",
)
```

## 14. 如何扩展

### 14.1 增加 Workflow

在 `agents/research_assistant/workflows/` 增加 YAML，并确保：

- `description` 能让 Router 与现有 Workflow 明确区分；
- `input_schema` 足够严格；
- 每条迁移都指向已声明 Node、`$complete` 或 `$fail`；
- 使用的 Operation 已由 Agent 绑定。

如果两个 Workflow 描述重叠，Router 的不确定性会直接变成产品行为问题，因此应优先收紧路由边界，而不是增加复杂分类代码。

### 14.2 增加 Node

确定性步骤优先使用 `operation` Node；只有“目标稳定、解决路径不确定、确实需要多步规划”的复合步骤才使用 `autonomous` Node。

Autonomous Node 至少需要：

- 清晰目标；
- 输入引用；
- 最小完成 Schema；
- 显式工具白名单；
- 合理 `max_steps`；
- `completed` 和 `failed` 迁移。

不要为 LLM 调用、人工交互或验证单独发明 Node 类型。它们可以由 Operation 或 Autonomous Node 内的现有协议承载。

### 14.3 增加 Operation

Operation 由 JSON Schema、风险等级、副作用、幂等性、调用预算和 handler 组成。新增搜索 provider 时，优先修改搜索 Operation 内部实现；只有产生新的稳定业务语义时才新增 Workflow Operation。

## 15. 失败与恢复语义

| 情况 | 行为 |
| --- | --- |
| 单个 provider blocked/failed | 其他 provider 继续；结果记录 limitation |
| 至少两个 provider 健康但无证据 | 问题可记录为 limited，不推断主体不存在 |
| 可用 provider 不足 | 标记搜索不可判定，而不是“没有结果” |
| Finding 引用未被当前 task 观察 | Operation 失败，Agent 在预算内修复 |
| `complete_node` Schema 或证据校验失败 | completion rejected，反馈给 AgentLoop |
| TaskPlan 尚有开放问题 | 拒绝完成 |
| 最后问题在步数上限关闭 | 仅保留一个 completion-only step |
| 开放计划耗尽步数 | `max_auto_steps_reached`，沿 failed transition |
| 用户修改 Scope | 保存反馈，同一 Checkpoint 重新规划 Scope |
| 用户取消 | Workflow cancelled |

## 16. 测试策略

当前测试重点覆盖：

- Agent discovery、Workflow 数量和 Operation 绑定；
- Router 对简单、深度和非研究请求的选择；
- Scope Review、修改和恢复；
- 多问题 TaskPlan 的逐项关闭；
- 搜索调用预算和 provider 隔离；
- `verify_claim_evidence` 的去重、`unrelated` 丢弃、来源出处复核（拒绝未被该 `task_id` 搜索观测到的 URL）和独立性域名复核拒绝/修正循环；
- `confidence.py` 六因子表和 `min()` 组合规则，含单一差因子拉低整体的用例，作为不依赖 Workflow 的纯函数单测；
- `unverifiable_flag` 问题直接记录 `blocked` 且不触发搜索；
- Finding 引用必须来自已观察来源；
- Harness 自动组装最终 Findings、citations 和 confidence；
- `build_evidence_graph` 输出结构与 `key_findings` 一致（每条边对应真实证据）；
- Limited 问题继续执行并进入最终限制；
- TaskPlan 步数上限附近关闭最后问题、随后仅完成节点的边界；
- 单框 CLI Live 生命周期和隐藏噪声；
- Trace、Checkpoint 和 terminal 状态。

新增能力时，应优先增加端到端 Agent 回归，再补 Runtime 的不变量单测。仅测试模型输出文本不能证明 Workflow、证据和恢复语义正确。

## 17. 关键文件

| 文件 | 作用 |
| --- | --- |
| `agents/research_assistant/agent.toml` | Agent factory 声明 |
| `agents/research_assistant/agent.py` | Agent 组合根 |
| `agents/research_assistant/workflows/*.yaml` | 三个稳定 Workflow |
| `agents/research_assistant/tools/research.py` | 搜索、证据标注、Finding、证据图谱和拒绝 Operation |
| `agents/research_assistant/confidence.py` | 六因子离散置信度纯函数模块 |
| `agents/research_assistant/skills/web-research/SKILL.md` | 模型研究方法与约束 |
| `src/modi_harness/workflow/router.py` | Workflow 选择与输入校验 |
| `src/modi_harness/workflow/runtime.py` | Node、TaskPlan、completion 和迁移语义 |
| `src/modi_harness/workflow/session.py` | Session、Checkpoint、Stream 和 Trace |
| `src/modi_harness/cli/renderer.py` | 单框 CLI 进度展示 |

## 18. 结论

当前 Research Assistant 的核心不是“会调用搜索工具的聊天机器人”，而是一套受 Workflow 约束、以 TaskPlan 组织问题、以 Finding 固化证据、由 Harness 决定完成的研究执行协议。

它保留了 Agent 对研究路径、查询改写和综合判断的自主性，同时把路由、权限、预算、证据归属、状态恢复和完成条件放在确定性 Runtime 中。这正是使用 Harness 而不是直接调用通用 Chat 模型的主要价值。
