# Research Assistant 优化设计

状态：问题定义，尚未选择最终方案。

## 1. 背景

当前 Research Assistant 的 `agent.md` 共 217 行，同时承担了五种职责：

- Agent 的身份与研究目标；
- 用户交互流程；
- `PLAN → FETCH → EVIDENCE → SUBMIT` 状态机；
- Harness 任务工具的调用顺序；
- 证据格式与最终 JSON Schema。

这使 Agent 定义变成了用自然语言编写的工作流程序。它具备代码的
复杂度，却缺少代码的类型检查、状态验证和可测试边界。Agent 作者必须
同时理解研究方法、Harness 协议、Tool 顺序和输出控制，因而难以判断一处
修改会影响什么。

本次优化限定在 `agents/research_assistant/` 内，不修改 Modi Harness。

## 2. 已确认的产品目标

Research Assistant 应支持两种研究范围：

1. 默认优先使用用户给定的来源；
2. 现有来源不足时，指出具体证据缺口并请求用户授权扩展来源。

只有用户明确同意后，Agent 才能搜索新来源。用户拒绝扩源时，Agent 应在
现有证据边界内回答，而不是把拒绝视为执行失败。

其他已确认目标：

- 用户通常先提出研究目标；只有用户仅提供 URL 时，Agent 才建议问题；
- 过程展示简短、有意义的研究进度，不展示内部状态机；
- 默认输出适合人阅读的自然简报；
- 用户明确要求时可以输出 JSON，但暂不承诺 Schema 强校验；
- 默认使用用户的语言；
- 结论必须区分事实、推断、来源分歧和未知；
- `agents/research_assistant/` 下的 Agent、Skills、Tools 都可以调整。

## 3. 分层原则

```text
Agent：我是谁、要完成什么、遵守什么边界
  ↓ 选择
Skill：遇到某类研究问题时，采用什么专业方法
  ↓ 使用
Tool：执行一个具体、可验证的外部动作
```

### Agent

Agent 只定义研究使命、用户授权边界、输出原则以及使用哪些 Skills。
非技术用户应能在一分钟内理解并修改它。

### Skill

Skill 承载可复用的专业方法，但不能只是把一个巨大 Prompt 拆成几个文件。
每个 Skill 必须主题单一，并且可以被其他 Agent 独立复用。

初步划分为：

- `research-framing`：明确问题、来源覆盖和证据缺口；
- `source-evaluation`：评估来源、证据、冲突和适用边界；
- `briefing-writing`：组织自然简报或按用户要求输出 JSON。

### Tool

Tool 只执行动作并返回结果，不决定研究策略：

- `fetch_url(url)`：获取并清理指定页面；
- `search_web(query, limit)`：使用 DuckDuckGo HTML 返回候选来源。

当前 `source_extract` 机械选择页面前部句子，不理解研究问题，并与模型的
证据判断重复，计划删除。

## 4. 目标研究流程

```text
接收用户目标
  ↓
问题是否明确？
  ├─ 否：请求澄清
  └─ 是
      ↓
是否已有来源？
  ├─ 否：说明需要搜索并请求授权
  └─ 是：获取并评估给定来源
              ↓
        证据能否回答核心问题？
          ├─ 是：形成结论
          └─ 否：说明具体缺口并请求扩源
                    ├─ 同意：搜索、获取并继续研究
                    └─ 拒绝：基于现有证据给出有限结论
```

单个来源失败不应终止整个研究。所有给定来源都失败时，应请求用户更正或
替换。搜索失败时，应说明无法扩源并返回已有证据边界。来源冲突时保留
双方，不在缺少依据时擅自选边。

## 5. 当前未决问题：Task Protocol 与自然简报冲突

### 5.1 当前配置

Research Assistant 使用原生任务协议展示进度：

```yaml
task_protocol:
  mode: required
  review: never
  min_items: 3
  max_items: 4
```

当前 Agent 同时声明结构化 `output_contract`，因此 Harness 会注入
`submit_output` Tool。任务全部完成后，Harness 进入 finalization，要求模型
只调用 `submit_output`，这条路径能够闭合。

### 5.2 为什么删除静态 Output Contract 后出现冲突

已确认的产品目标是默认输出自然简报，因此计划删除静态
`output_contract`。删除后 Harness 不会注入 `submit_output` Tool。

但是，只要任务计划全部完成，当前 `ContextManager` 仍会进入
finalization，并注入以下含义的指令：

```text
调用 submit_output 完成提交，不要输出普通文本，也不要调用其他 Tool。
```

模型此时看到的状态是：

```text
系统要求：必须调用 submit_output
实际可用：没有 submit_output Tool
```

因此以下三个目标在“不修改 Harness”的约束下不能直接同时成立：

```text
原生 task_protocol
+ 默认自然简报
+ 无静态 output_contract
```

### 5.3 可能表现

- 模型直接输出自然简报，与 finalization 指令冲突；
- 模型尝试调用不存在的 `submit_output`；
- 模型重复尝试，最终耗尽 repair 或 max steps；
- 不同模型对冲突采取不同策略，导致行为不稳定。

### 5.4 待选择方案

#### 方案 A：删除 Task Protocol

删除 `task_protocol` 和静态 `output_contract`。进度通过简短自然语言以及
可见的 `fetch_url` / `search_web` 活动表达，最终直接输出自然简报。

优点：最符合面向人的产品目标，Agent 定义最简单。

代价：不再获得 Harness 原生任务列表和任务状态事件。

#### 方案 B：保留 Task Protocol 与结构化提交

继续保留 `task_protocol`、`output_contract` 和 `submit_output`，由客户端把
结构化结果渲染为自然简报。

优点：保留任务进度、Schema 校验和稳定的自动化输出。

代价：Agent 的默认输出仍由固定数据结构主导，不符合当前确认的自然简报
方向。

#### 方案 C：拆成两个 Agent

- `research-assistant`：自然研究过程和自然简报；
- `research-assistant-json`：Task Protocol、结构化契约和强校验。

优点：两种使用场景都具有清晰、稳定的契约。

代价：增加 Agent 数量、发现结果和维护成本；两者之间可能产生重复规则。

#### 方案 D：修改 Harness

让 free-form Agent 在任务完成后进入自然文本 finalization，而结构化 Agent
才要求 `submit_output`。

优点：从运行时根治冲突，并保留原生任务进度。

代价：超出本次“只优化 Research Assistant、不修改 Harness”的范围。

## 6. 暂不决定的内容

在上述冲突得到明确选择前，不确定：

- `agent.md` 是否保留 `task_protocol`；
- 最终进度由 Harness 任务事件还是自然语言承担；
- 是否保留或拆分结构化输出能力；
- 最终 Agent 文件和测试应采用哪条闭环路径。

当前不修改 `agent.md`、Skills、Tools 或测试。
