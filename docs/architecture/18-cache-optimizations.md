# Provider Cache 策略与优化方法
## 1. 总体分档
|Provider|缓存策略|客户端可控性|优化重点|
|---|---|--:|---|
|Claude|显式 Prompt Caching|高|`cache_control` + 稳定前缀|
|OpenAI|自动 Prompt Caching|中|稳定前缀 + `prompt_cache_key`|
|DeepSeek|默认磁盘 Context Cache|低|保持重叠前缀稳定|
|Qwen / 百炼|显式 + 隐式 Context Cache|高|显式 cache marker + 前缀稳定|
|聚合商 / 第三方 Gateway|不确定|不确定|必须实测|
---
# 2. Claude / Anthropic
## 策略
```text
显式缓存。
通过 cache_control 标记缓存断点。
服务端返回 cache_creation_input_tokens / cache_read_input_tokens。
```
Anthropic 官方文档说明，Prompt Caching 支持在请求中设置 `cache_control`，并返回缓存创建和缓存读取 token 数据；缓存按照 `tools → system → messages` 的顺序处理前缀。([Developer Documentation](https://developers.llamaindex.ai/python/framework/integrations/llm/anthropic_prompt_caching/?utm_source=chatgpt.com "Anthropic Prompt Caching | Developer Documentation"))
## 可行优化
```text
1. 固定 tools
2. 固定 system prompt
3. 固定 message 顺序
4. 把静态长上下文放在 cache_control 前
5. 把用户输入、时间戳、临时变量放后面
6. 对长任务做缓存预热
7. 用 cache_read_input_tokens 验证收益
```
## 判断
```text
Claude 最适合做客户端缓存结构优化。
```
---
# 3. OpenAI
## 策略
```text
自动缓存。
客户端不能显式创建缓存。
命中依赖 prompt 前缀一致。
可用 prompt_cache_key 影响路由，提高命中概率。
usage 返回 cached_tokens。
```
OpenAI 官方文档说明，Prompt Caching 自动生效，`prompt_cache_key` 会与前缀 hash 结合，用于影响请求路由、提升长公共前缀场景下的命中概率。([OpenAI开发者](https://developers.openai.com/api/docs/guides/prompt-caching?utm_source=chatgpt.com "Prompt caching | OpenAI API"))
## 可行优化
```text
1. 稳定前缀
2. 静态内容前置
3. 动态内容后置
4. 固定 instructions / tools / examples
5. 同类任务设置稳定 prompt_cache_key
6. 避免 base64、大文件、随机内容进入前缀
7. 用 cached_tokens 验证收益
```
## 判断
```text
OpenAI 可优化，但不能保证命中。
重点是前缀稳定和路由一致。
```
---
# 4. DeepSeek
## 策略
```text
默认开启磁盘 Context Cache。
不需要客户端配置。
命中基于与历史请求的重叠前缀。
返回 prompt_cache_hit_tokens / prompt_cache_miss_tokens。
```
DeepSeek 官方文档说明，Context Caching on Disk 默认对所有用户开启；后续请求如果与之前请求有重叠前缀，重叠部分会从缓存读取，计为 cache hit。([DeepSeek API 文档](https://api-docs.deepseek.com/guides/kv_cache?utm_source=chatgpt.com "Context Caching"))
## 可行优化
```text
1. 保持 system prompt 稳定
2. 保持 tools 顺序稳定
3. 保持历史摘要格式稳定
4. 把变化内容后置
5. 避免时间戳、随机 ID、动态说明进入前缀
6. 避免每轮重写长 system / tools
7. 用 prompt_cache_hit_tokens / miss_tokens 验证收益
```
## 判断
```text
DeepSeek 客户端控制弱。
主要做前缀稳定，收益靠实测。
```
---
# 5. Qwen / 阿里百炼
## 策略
```text
支持显式缓存和隐式缓存。
显式缓存需要手动标记，命中更可控。
隐式缓存自动识别公共前缀，但不保证命中。
```
阿里云 Model Studio 文档说明，Qwen Context Cache 支持 Explicit cache 和 Implicit cache；显式缓存需要手动设置，成本更高但命中更确定，隐式缓存自动工作但命中不保证。([阿里云](https://www.alibabacloud.com/help/en/model-studio/context-cache?utm_source=chatgpt.com "Context Cache feature for Qwen models"))
阿里云模型文档还说明，Qwen 部分模型支持 context cache，隐式缓存命中按标准输入价 20% 计费，显式缓存命中按 10% 计费。([阿里云](https://www.alibabacloud.com/help/en/model-studio/models?utm_source=chatgpt.com "Supported Models and Capabilities Overview - Model Studio"))
## 可行优化
```text
1. 对长 system / tools / 项目上下文设置显式 cache marker
2. 静态内容前置
3. 动态内容后置
4. 控制 cache marker 位置
5. 避免显式缓存块频繁变化
6. 显式缓存用于长任务，隐式缓存用于轻量重复任务
7. 监控 cached input / cache hit 数据
```
## 判断
```text
Qwen 可控性高。
适合在 Modi-Harness 里做 Provider Cache Adapter。
```
---
# 6. 第三方 Gateway / 聚合商
## 策略
```text
高度不确定。
可能转发缓存字段，也可能吞字段。
可能保持原始请求，也可能改写请求。
可能破坏前缀稳定性。
```
## 可行优化
```text
1. 必须实测 cache_read / cached_tokens 是否返回
2. 对比直连 Provider 与聚合商缓存命中差异
3. 检查是否改写 messages / tools / system
4. 检查是否保留 cache_control / prompt_cache_key
5. 关键场景优先直连 Provider
```
## 判断
```text
聚合商不能默认信任。
缓存能力必须以日志实测为准。
```
---
# 7. 通用优化原则
```text
1. 静态内容前置
   system、tools、规则、项目说明、固定示例放前面。
2. 动态内容后置
   user input、时间戳、随机 ID、临时任务状态放后面。
3. 前缀稳定
   不要每轮重排 messages、tools、memory、RAG。
4. 工具瘦身
   不要每次暴露全部 tools，只暴露当前任务需要的工具。
5. 历史压缩
   长历史不要全量带，改成阶段摘要 + 最近 N 轮。
6. RAG 控制
   控制 TopK，去重，压缩片段，避免全文塞入。
7. Provider 适配
   Claude / Qwen 用显式缓存；
   OpenAI 用 prompt_cache_key；
   DeepSeek 保持公共前缀稳定。
8. 用数据验证
   不看感觉，只看 cache_read_tokens / input_tokens。
```
---
# 8. 最终结论
```text
Claude / Qwen：可以主动设计缓存。
OpenAI：只能提高自动缓存命中概率。
DeepSeek：只能保持前缀稳定并观察命中。
聚合商：必须实测，不可假设。
```
一句话：
> **客户端不能控制缓存，只能提升缓存友好度；真正是否命中，必须由 Gateway 日志验证。**