# Deep Search 设计

状态：当前实现  
更新：2026-07-23

## 1. 目标与原则

Deep Search 用于需要多次检索、比较、尽调或综合判断的公开资料研究。首要目标是产出直接、完整、可引用的答案，而不是完成复杂的规划和验证仪式。

- 问题清楚时立即搜索，不要求用户预先确认 Scope。
- 先探索真实资料，再建立研究任务。
- Task 表示信息缺口，不是报告章节。
- 时间优先用于搜索、阅读、整理和输出。
- 模型负责查询、选源和语义判断；Runtime 负责权限、Task 身份及 canonical evidence/provenance。
- 模型输出缺少机械字段时由确定性代码补齐，不为格式反复消耗模型步骤。

本设计不引入知识图谱、向量库、统一报告模板，也不重写通用 Task Graph、child 隔离、checkpoint 和 Tool Gateway。它不增加由模型反复执行的逐条 evidence validation，但保留 Runtime 的确定性证据校验。

## 2. 当前流程

```text
理解问题 -> 广泛探索 -> 建立研究地图 -> 多任务深入研究 -> 统一综合输出
```

对应 Workflow：

```text
understand -> current_time -> explore -> map_research -> initialize
           -> investigate -> synthesize -> finalize_report
```

`current_time`、`initialize` 和 `finalize_report` 是技术节点，不是额外研究阶段。

| 阶段 | 节点 | 产物 |
| --- | --- | --- |
| 理解问题 | `understand` | Research Brief、4–6 条探索查询 |
| 广泛探索 | `explore` | 查询记录、最多 6 个可用来源 |
| 建立地图 | `map_research` + `initialize` | Landscape、Coverage、初始 Task Graph |
| 深入研究 | `investigate` | committed Finding、来源、新问题 |
| 综合输出 | `synthesize` + `finalize_report` | 答案、引用、限制 |

## 3. 阶段行为

### 3.1 理解与探索

Research Brief 只保留原始请求、目标、任务类型、研究主体、时效、用户约束和实质歧义。上游、中游、应用场景、龙头企业等是研究维度，不是实体。普通表达不清通过搜索消歧，不在搜索前展开完整规划。

`understand` 同时生成 4–6 条目的互补查询，覆盖直接问题、核心对象或别名、官方或权威资料，以及必要的时效和消歧方向。查询不能只是同义改写。

`public_web_explore` 批量搜索免费公开网页；配置豆包后可加入豆包搜索。单个 provider 失败时继续使用其他健康 provider。探索只建立资料概览，不生成最终结论。

### 3.2 研究地图

`map_research` 只输出主题、资料概览、Coverage 问题和最多 4 个建议 Task，不负责 ID、状态、默认理由、优先级或实体绑定。

`initialize_deep_research` 将这份 Research Map 规范化为 1–4 个首轮 Task：已有 Task 来自 `map_research.tasks`，初始化 Operation 将其与 Coverage Map 绑定；任务缺失或覆盖不全时，再按 Coverage 生成、合并兜底任务。已成功提交但字段不完整或为空的草图不会使探索结果失效。

Coverage 表示最终答案必须覆盖什么，Task 表示当前要搜索什么。一个 Task 可推进多个 Coverage item，一个 Coverage item 也可由多个 Task 补齐；Task 完成不自动等于答案覆盖。

“完整产业链”会确定性补齐：上游、中游、下游、支撑生态、龙头企业与竞争格局、商业化与量产、技术和规模化瓶颈。

### 3.3 Task Graph 深入研究

`investigate` 使用通用 Task Graph Runtime，当前上限为 8 个 Task、3 次 replan、4 个 child 并发和 10 次 child run。

每个 child 获得有界上下文：Research Brief、Landscape、Coverage、当前 Task、最多 6 个探索来源、最近 6 个 committed result、最近 5 条用户 steering 和依赖输出。

child 的执行规则：

1. 先复用已有资料；
2. 仍有缺口时用 1–2 条互补查询直接调用一次 `public_web_search`；Runtime 在同一步自动取时并注入单次令牌；
3. 只有搜索明确给出质量缺口或发现关键冲突时，才补搜一次；
4. 搜索额度用完后，基于已有材料提交 Finding 草稿，由 Runtime 判定为 `sourced` 或 `blocked`。

搜索额度按实际调用次数计算，成功、空结果、provider 失败和整体超时都消耗额度。时间令牌由 Runtime 管理，不占模型步骤。达到额度后，Brain 不再暴露搜索，只允许 child 基于探索来源和已有结果提交 Finding；如果没有可用来源，则提交带明确 limitation 的 `blocked` Finding。最后一次有界搜索即使失败，也必须保留一个结果提交步骤。

草稿只强制包含 conclusion；implications、最多 4 个 source URL、limitations 和 suggested work 都可选。Runtime 从实际搜索结果补齐来源和机械字段，并根据该 Task 的证据覆盖确定 `sourced` 或 `blocked`。相同完成错误连续出现两次时立即以类型化原因终止，不再把剩余步骤浪费在同一 schema repair 上。

Runtime 将共享的探索来源、已有 committed result 来源和本 Task 的搜索来源合并为同一个可引用来源集，再注入 canonical evidence、citations、confidence、verification method、provenance 和 task resolution。Finding 可以混合引用这些来源；Runtime 必须保留各自的搜索 provenance，而不能因来源跨阶段就要求模型重写结果。child 不手写这些字段，也不调用 `verify_claim_evidence`。

`suggested_work` 只在首轮 Task 全部进入终态后考虑，并按 Coverage ID 和问题语义与现有 Task 去重；每轮最多增加两个真正的新缺口。每条用户反馈只替换一个最高优先级 pending Task；没有 pending 时新增一个 Task。它不会修改运行中的 Task，也不会重写整体 Intent。

研究维度 Task 是获得高质量答案的并行手段，不是任一失败就必须终止整张图的硬依赖。单个 child 失败时保留首次真实原因并继续其他 Task；只要至少一个完成的 Finding 足以通过核心 Criterion，就进入综合输出，同时把失败维度作为总体限制。只有没有任何可用 Finding 或核心 Criterion 最终无法满足时，Task Graph 才失败。

### 3.4 综合与引用

`synthesize` 读取研究地图、全部 committed result、来源和限制，正常情况下统一重写答案，不展示 Criterion、verification method、Task 状态或内部 confidence。若综合模型失败，固定 `finalize_fallback` 直接从已提交结果生成可交付答案。

`finalize_report` 绑定引用并生成来源列表。当前只有全部 Finding 都为 `sourced` 时才保留统一综合答案；只要存在一个 `blocked` Finding，就会退回按 Task 组织的确定性摘要。这是实现限制，不是目标输出形态。

## 4. 搜索与来源边界

来源选择随问题变化：

- 产品参数、价格、公司披露优先官方或一手来源；
- 法规和标准优先主管机构或标准组织；
- 学术概念优先论文、综述和权威学术资料；
- 企业与人物线索可结合机构页面、公开登记和可靠媒体；
- 正文不可读时，只允许内容充分的搜索摘录降级使用。

单次搜索执行 URL 规范化、去重、相关性筛选和质量排序。不同并行 child 之间尚无全局查询 lease，仍可能出现相近查询；Context Builder 只能通过共享探索来源和近期 Finding 减少重复。

公开资料缺失不能推导出主体不存在。不同时间、地区、版本和统计口径的数据不得静默混合。

## 5. 界面与失败处理

CLI 只展示阶段进度和 Task 状态，不展示 schema repair、GraphPatch、provider 重试或验证术语。

- `in_progress`：动态 spinner；
- `pending`：`○`；
- `completed`：`✓`；
- 受限完成或阻塞：`△`；
- 取消：`✗`。

非交互终端使用静态符号，避免动画控制字符污染日志。

当前恢复规则：

- provider 失败：切换其他健康 provider，并记录限制；
- 整体搜索超时：计入搜索额度；额度耗尽后停止重试并提交有限结果；
- 页面不可读：尝试其他来源或可用摘录；
- Research Map 草图缺字段：初始化 Operation 规范化并补齐；
- 查询额度耗尽：child 使用已有材料提交结果；
- Finding 草稿缺少 `status`：Runtime 根据实际绑定证据确定状态；
- Finding 草稿只有 conclusion：Runtime 补齐 implications、来源、limitations 和 provenance；
- 同一 completion feedback 连续出现：以 `completion_contract_stalled` 或 `model_returned_empty_result` 快速终止；
- 综合模型失败：从 committed result 确定性降级输出；
- Finding 混合引用探索来源和 Task 搜索来源：Runtime 合并绑定并分别保留 provenance；
- 单个研究 child 失败：记录真实原因，继续其余 Task，并在最终答案中披露受影响维度；
- 模型超时或进程中断：依赖现有 checkpoint 恢复；
- 资料不足：给出有限答案并明确搜索边界。

## 6. 当前已知缺口

- `understand` 和 `map_research` 仍是 autonomous node；未在步数内提交合法结果会使运行失败。Research Map 补全只能处理已经成功提交的草图。
- 完成判断仍主要依赖核心 Criterion 和 committed Finding，不是独立的 Coverage Goal Evaluator。
- 尚无跨并行 child 的原子查询 reservation 和抓取结果复用。
- 尚无基于搜索边际收益的精确停止策略。
- user steering 不能修改运行中 Task 或整体 Intent。
- `material_ambiguities` 当前只进入上下文，不会自动暂停并向用户澄清。
- 存在 `blocked` Finding 时，finalizer 会丢弃统一综合答案并退回 Task 摘要。

后续优化应优先解决这些结构性问题，不应重新增加模型验证仪式。

## 7. 验收与维护

- 清晰问题无需输入 `go` 即开始探索。
- 探索包含 4–6 条目的不同查询。
- Research Map 缺少机械字段不会耗尽自动步骤。
- Finding 缺少 Runtime 可派生的机械字段不会触发模型重试。
- “完整产业链”不会遗漏关键环节、龙头企业、商业化和瓶颈。
- Task 围绕真实信息缺口，最终答案引用实际可用来源。
- 单个 provider 失败不会立即终止搜索。
- 时间主要用于搜索和综合，而不是 Scope、schema repair 和重复验证。

五阶段流程是稳定顶层结构。后续修改本文件时，以当前代码和 trace 为准：已实现行为写入正文，未实现方向只写入“当前已知缺口”。
