# Research Assistant 自然简报重构设计

状态：设计已确认，待用户复核后转入实现计划。

## 1. 背景与目标

当前 `agents/research_assistant/agent.md` 共 217 行，把研究助手写成了一个用中文
prompt 编写的工作流程序：它同时承担身份、用户交互、`PLAN → FETCH → EVIDENCE →
SUBMIT` 状态机、Harness 任务工具调用顺序、证据格式与最终 JSON Schema。它具备代码
的复杂度，却缺少代码的类型检查、状态验证和可测试边界。

这与全局 model-first 原则冲突：Harness 应当**扩展模型能力，而非与模型推理竞争**；
模型是主体，Harness 是执行衬底。Agent prompt 不应是替模型写死的状态机。

本次重构限定在 `agents/research_assistant/` 内，**不修改 Modi Harness**。

### 已确认的产品目标

- 默认优先使用用户给定来源；来源不足时指出**具体证据缺口**并请求授权扩源。
- 只有用户明确同意后才搜索新来源；用户拒绝扩源时，在现有证据边界内回答，不当失败。
- 用户通常先提出研究目标；只有仅提供 URL 时，Agent 才主动追问研究问题。
- 进度展示简短、有意义，不展示内部状态机。
- 默认输出适合人阅读的自然简报；用户明确要求时输出 JSON 文本（不承诺 Schema 强校验）。
- 默认使用用户语言。
- 结论必须区分事实、推断、来源分歧和未知。

## 2. 已确认的关键决策

§5 的 A/B/C/D 冲突方案中，本次采用 **方案 A**，并据此确定以下决策：

| 决策点 | 选择 |
| --- | --- |
| 进度展示 | 删除 `task_protocol`；进度由模型简短自然语言 + 可见的 `fetch_url`/`search_web` 调用承担 |
| 工具集 | 删除 `source_extract`，新增 `search_web`；最终 = `fetch_url` + `search_web` |
| 研究流程归属 | §4 流程放入新增 `research-framing` skill，模型自行决定何时使用 |
| 启动问答 | 保留 `interaction_protocol.startup: agent`，只给 URL 时主动追问研究问题 |
| JSON 输出 | 默认自然简报；用户要求时模型直接输出 JSON 文本，不走 `submit_output`，不强校验 |

### 冲突如何消失

删除 `task_protocol` 与静态 `output_contract` 后，Harness 既不进入 task
finalization，也不注入 `submit_output`。§5 中「系统要求调用一个不存在的工具」的死结
从根上不再存在。

## 3. 分层与文件职责

```text
Agent：我是谁、要完成什么、遵守什么边界、用哪些 skill
  ↓ 选择
Skill：遇到某类研究问题时采用什么专业方法
  ↓ 使用
Tool：执行一个具体、可验证的外部动作
```

### Agent（`agent.md`，目标 ≤ 50 行）

只回答四件事：

- 身份与研究使命；
- 授权边界：默认优先用户给定来源；来源不足时指出**具体证据缺口**并请求授权扩源；
  用户拒绝则在现有证据边界内回答，不当失败；
- 输出原则：默认自然简报，区分事实/推断/来源分歧/未知，默认用户语言；用户明确要求
  时输出 JSON 文本（不强校验）；
- 使用哪些 skill。

保留的前置元数据：`permission_profile.mode: auto`、`interaction_protocol.startup:
agent`、`deny: save_memory`。

删除的内容：`task_protocol`、静态 `output_contract`、PLAN/FETCH/EVIDENCE/SUBMIT 状态
机、所有 `create_task_plan` / `start_task` / `complete_task` / `submit_output` 调用顺序
规则。

非技术用户应能在一分钟内读懂并修改该文件。

### Skill

依赖方向：**framing 定问题与缺口 → evaluation 评单源 → writing 组织产出**，互不内嵌。

- **`research-framing`（新增）** — 承载 §4 研究流程方法：
  - 判断研究问题是否明确，不明确则澄清；只给 URL 时基于**已成功获取的页面内容**建议
    研究问题（不靠域名/品牌名猜测页面功能、定价或适用场景）；
  - 判断现有来源能回答什么、不能回答什么，识别**具体证据缺口**；
  - 缺口需扩源时，说清缺什么、为什么现有来源不够，再请求授权；用户拒绝则收窄到现有
    证据能回答的部分。
  - 边界：只做问题界定与缺口识别，不评估单个来源质量，不写简报。

- **`source-evaluation`（已存在，去状态机化）** — 评估单个来源的 kind/recency/
  conflicts，冲突时双方都留、不擅自选边。
  - 改动：删除所有「EVIDENCE 阶段」「不调用 source_extract」「不调用 submit_output」
    等绑死旧状态机的措辞，使其对流程无感、可独立复用。

- **`briefing-writing`（由现有 `briefing-structure` 改写）** — 组织最终产物：
  - 默认：自然简报，区分事实/推断/分歧/未知，结论绑定来源；
  - 用户要求时：输出 JSON 文本，结构清晰但不强校验。

### Tool

Tool 只执行动作并返回结果，不决定研究策略：

- `fetch_url(url)`：获取并清理指定页面（保留现状）；
- `search_web(query, limit)`：使用 DuckDuckGo HTML 返回候选来源（新增）。

删除 `source_extract`：它机械地选取页面前部句子，不理解研究问题，并与模型自己的证据
判断重复。

## 4. 研究流程数据流

```text
接收用户输入
  ↓
[startup: agent] 只给 URL? ──是──> 先 fetch_url 获取页面
  │                                    ↓
  │                          基于页面内容建议研究问题(request_user_input)
  │                                    ↓
  └─否(已有问题)──────────────────> 问题是否明确? ──否──> 澄清
                                          ↓是
                                    fetch 给定来源并评估
                                          ↓
                                  证据能回答核心问题?
                                    ├─能──> 形成结论
                                    └─否──> 说明具体缺口 + 请求扩源授权
                                              ├─同意──> search_web → fetch_url → 继续
                                              └─拒绝──> 现有证据边界内给有限结论
  ↓
自然简报(默认) 或 JSON 文本(用户要求)
```

进度由模型简短自然语言 + 可见的 `fetch_url`/`search_web` 调用承担，不再有任务列表事件。

## 5. 错误处理

- **单个来源 fetch 失败**：不终止整个研究，标注该来源不可用，继续其余来源。
- **全部给定来源失败**：请求用户更正或替换 URL，不提推测性结论。
- **search_web 失败**：说明无法扩源，退回现有证据边界，不当致命错误。
- **来源冲突**：双方都保留，缺少 primary 依据时不擅自选边（由 source-evaluation 承担）。
- **用户拒绝扩源**：当作正常边界，不当执行失败。

## 6. 测试策略

`runtime.py` 变更对应的测试调整：

- 删除 `source_extract` 及其 spec 与相关测试。
- 新增 `search_web(query, limit)`（DuckDuckGo HTML），单元测试覆盖：正常返回候选、
  网络失败返回 error dict、limit 边界。
- `build_agent()` 测试：断言加载三个 skill（research-framing / source-evaluation /
  briefing-writing），工具集为 `fetch_url` + `search_web`，且元数据不再含
  `task_protocol` / `output_contract`。
- 沿用 `fetch_url` 现有测试。
- 「拒绝扩源后给有限结论」等行为需要 agent 级集成测试，成本较高；本次以单元测试为主，
  行为类断言在实现阶段按性价比决定是否补充。

## 7. 范围边界

- 仅改动 `agents/research_assistant/` 下的 agent.md、skills、runtime.py 及对应测试。
- 不修改 Modi Harness。
- DuckDuckGo HTML 抓取仅作为候选来源发现，不替代模型对来源质量的判断。
