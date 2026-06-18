# modi-harness 核心检查清单：Harness 与 Agent 边界

## 一、总判断

modi-harness 的核心定位应是：

> Harness 管执行控制，Agent 管场景语义，Model 管智能生成。

如果一个模块既写了业务语义，又控制执行状态、工具权限、上下文构造、校验和成本，那边界已经混乱。

---

## 二、边界检查总表

| 检查项              | 应归属                   | 通过标准                           | 风险信号                      |
| ---------------- | --------------------- | ------------------------------ | ------------------------- |
| 任务状态管理           | Harness               | 每个 run / step 可追踪、可恢复          | Agent 自己维护状态              |
| 工具执行控制           | Harness               | 工具调用必须经过权限、参数、超时、重试控制          | Model / Agent 直接执行工具      |
| 上下文构造            | Harness 主控，Agent 提供策略 | Harness 知道模型看了什么、为什么看          | Agent 随意拼 prompt / memory |
| 模型调用             | Harness               | 统一模型路由、调用、日志、成本统计              | Agent 直接调模型               |
| Prompt 模板        | Agent                 | 不同场景有独立 prompt                 | Harness 核心代码里写业务 prompt   |
| 业务规则             | Agent                 | 规则由 Agent 声明或注入                | Harness 写死公安、投研、日志等业务逻辑   |
| 输出 Schema        | Agent 定义，Harness 校验   | Agent 提供格式，Harness 强校验         | 靠 prompt 让模型“尽量输出 JSON”   |
| Memory namespace | Agent 定义，Harness 管理   | 不同 Agent 记忆隔离                  | 所有 Agent 共用混乱 memory      |
| Retry 策略         | Harness               | 失败类型明确，局部重试                    | Agent / Model 自由循环        |
| 成本统计             | Harness               | 每步 token / latency / cache 可解释 | 只能看到最终结果，看不到消耗            |
| 评测框架             | Harness               | 支持统一回归评测                       | 每个 Agent 自己搞评测            |
| 评测用例             | Agent                 | 每个场景有自己的 case                  | Harness 内置所有业务 case       |

---

## 三、Harness 必须管什么

以下内容如果不在 Harness，就不叫 Harness。

### 1. State

必须回答：

```text
当前任务是什么？
执行到哪一步？
上一步输入是什么？
上一步输出是什么？
失败在哪里？
能不能恢复？
```

最低字段：

```text
run_id
step_id
task_type
status
input_ref
output_ref
error
timestamp
```

检查问题：

| 问题                 | 结论 |
| ------------------ | -- |
| 每次模型调用是否有 step_id？ |    |
| 工具调用是否能关联到具体 step？ |    |
| 失败后是否知道失败节点？       |    |
| 是否支持局部重试，而不是整链路重跑？ |    |

---

### 2. Tool Gate

核心原则：

> Model / Agent 只能提出 tool_use，Harness 决定是否执行。

检查问题：

| 问题                           | 结论 |
| ---------------------------- | -- |
| 工具调用前是否有权限判断？                |    |
| 工具参数是否校验？                    |    |
| 工具是否有 timeout？               |    |
| 工具失败是否有结构化错误？                |    |
| 是否限制最大工具调用次数？                |    |
| 高风险工具是否支持 confirm / dry-run？ |    |

红线：

```text
Agent 直接执行工具 = 边界失败
Model 自主无限调用工具 = 边界失败
工具失败直接丢给模型自由处理 = 边界失败
```

---

### 3. Context Control

核心原则：

> Context 不是聊天记录堆叠，而是模型执行输入。

Harness 必须知道：

```text
用了哪些 user input
用了哪些 memory
用了哪些 tool result
用了哪些文件
用了多少 token
哪些内容被裁剪
哪些内容被缓存
```

检查问题：

| 问题                              | 结论 |
| ------------------------------- | -- |
| context 是否有 hash？               |    |
| memory 是否有 namespace？           |    |
| 同一 run 内 memory 是否避免重复召回？       |    |
| tool result 是否经过压缩或选择？          |    |
| 是否记录 context token？             |    |
| 是否支持 cache-aware context build？ |    |

红线：

```text
Agent 自己随意拼 prompt = 边界失败
Memory 每轮无脑注入 = 上下文失控
长上下文无脑塞满 = 成本失控
```

---

### 4. Validation

核心原则：

> Model 生成结果，Harness 判断结果是否可用。

检查问题：

| 问题                  | 结论 |
| ------------------- | -- |
| 是否有 output_schema？  |    |
| 是否有强校验器？            |    |
| 是否区分格式错误、字段错误、业务错误？ |    |
| 是否支持局部 repair？      |    |
| 是否限制 repair 次数？     |    |
| 校验失败是否有结构化日志？       |    |

红线：

```text
靠 prompt 保证 JSON = 不合格
模型自己判断输出是否合格 = 不合格
校验失败后整链路重跑 = 不合格
```

---

### 5. Trace / Cost

核心原则：

> 没有 trace，就没有优化；没有 cost，就没有 Token Efficiency。

必须记录：

```text
model_name
provider
input_tokens
output_tokens
cache_read_tokens
cache_write_tokens
model_latency
tool_latency
context_hash
prompt_hash
retry_count
validation_error
```

检查问题：

| 问题               | 结论 |
| ---------------- | -- |
| 能否知道哪一步最贵？       |    |
| 能否知道哪一步最慢？       |    |
| 能否知道为什么重试？       |    |
| 能否知道上下文是否重复？     |    |
| 能否知道 cache 是否命中？ |    |

红线：

```text
只有最终回答，没有过程数据 = 不具备 Harness 价值
不能解释 token 消耗 = 不具备 Token 效率工程价值
```

---

## 四、Agent 应该管什么

Agent 不应该实现运行时，但必须提供场景语义。

Agent 应该声明：

```text
agent_id
task_types
prompt_templates
allowed_tools
context_policy
memory_namespace
output_schema
business_rules
eval_cases
risk_policy
```

检查问题：

| 问题                            | 结论 |
| ----------------------------- | -- |
| Agent 是否有独立 agent_id？         |    |
| Agent 是否声明支持哪些 task_type？     |    |
| Agent 是否声明可用工具，而不是直接执行工具？     |    |
| Agent 是否提供 output_schema？     |    |
| Agent 是否提供业务校验规则？             |    |
| Agent 是否有独立 memory namespace？ |    |
| Agent 是否有评测用例？                |    |

红线：

```text
Agent 只是一个 prompt = 太弱
Agent 自己实现状态、工具、模型调用 = 太重
Agent 逻辑散落在 Harness 核心代码 = 污染 Harness
```

---

## 五、最关键的边界判断题

逐条问自己：

### 1. 新增一个 Agent，要不要改 Harness 核心代码？

通过标准：

```text
不需要改 Kernel，只新增 Agent 配置 / Package / Schema / Prompt / Eval。
```

如果需要大量修改 Harness 核心代码，说明 Harness 和 Agent 没解耦。

---

### 2. Agent 能不能绕过 Harness 直接调用模型？

通过标准：

```text
不能。所有模型调用必须经过 Harness 的 Model Runtime。
```

否则无法统一统计 token、cache、latency、失败原因。

---

### 3. Agent 能不能绕过 Harness 直接调用工具？

通过标准：

```text
不能。Agent 只能声明工具需求，Harness 负责执行。
```

否则权限、审计、失败恢复都会失控。

---

### 4. Harness 里有没有写死业务逻辑？

通过标准：

```text
Harness 只认识 task_type、schema、policy、tool、state，不直接认识具体业务。
```

例如 Harness 不应该写死：

```text
公安案件阶段
投研事件链路
日志错误分类
论文评分标准
代码修复规范
```

这些应属于 Agent。

---

### 5. Agent 失败后，是谁决定怎么重试？

通过标准：

```text
Harness 根据错误类型和 retry policy 决定。
Model / Agent 只负责修复具体内容。
```

否则会出现模型自由循环、token 失控。

---

## 六、最终通过标准

modi-harness 合格的最低标准：

| 核心能力           | 必须结果               |
| -------------- | ------------------ |
| State          | 每一步可追踪             |
| Tool           | 每次工具调用可控           |
| Context        | 每次模型输入可解释          |
| Validation     | 每次输出可校验            |
| Trace          | 每次执行可回放            |
| Cost           | 每次消耗可归因            |
| Agent Boundary | Agent 不污染 Harness  |
| Extensibility  | 新增 Agent 不改 Kernel |

最终判断：

```text
Harness 不负责业务聪明。
Harness 负责系统可控。

Agent 不负责运行时。
Agent 负责场景语义。

Model 不负责治理。
Model 负责智能生成。
```

一句话检查：

> 如果 modi-harness 能让任意 Agent 在统一状态、统一工具、统一上下文、统一校验、统一日志、统一成本体系下运行，它就是 Harness；否则只是 Agent 封装。

```
```
