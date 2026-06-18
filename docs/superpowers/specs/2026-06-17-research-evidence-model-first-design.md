# Research Assistant 证据质量 · 第 1 条:证据原材料从「Harness 挑」改为「模型挑」

- 日期:2026-06-17
- 范围:`examples/research_assistant/`(工具函数 + agent.md + source-evaluation skill)与对应 `tests/examples/`
- 不动:`src/modi_harness/`(Harness 内核一行不改)

## 1. 背景与问题

研究助手 example 的执行效率已经验证充分(行为收敛、工具调用收敛、可观测、输出稳定),但**简报内容质量低**。

根因定位在证据抽取管线,不在 prompt、也不在输出 caps:

- `fetch_url`(`run.py:106`)内部调用 `source_extract` → `_select_evidence_facts`(`run.py:157-175`),后者把正文按句子切分后**取前 8 个长度 ≥40 字的句子,原样采用**。
- 这一步**完全不看研究问题**:无论问什么,返回的都是页面开头的定义/铺垫句。
- `content_preview = body[:1800]`(`run.py:73,114`)同样是「取头部 N」,长文还没到正题就被截断。
- `_source_title = content[:120]`(`run.py:178`)把正文前 120 字符冒充标题,而非真正的 `<title>`。

本质是 Harness 用正则启发式替模型做了「哪句是证据」的语义判断 —— 既做得差,又违反「模型为主体」第一性原理(见 `[[principle_model_first]]`)。垃圾进垃圾出。

## 2. 第一性原理下的方向

**Harness 退后,只做无损清洗,把「哪些内容是证据」的语义判断交还模型。**

- Harness 职责:抓取、降噪(去脚本/导航/页脚)、抓真标题、按固定额度兜底上下文预算。**不再挑证据。**
- 模型职责:在 EVIDENCE 阶段读完干净正文,自己产出证据草稿。

这与既有的 V0.6.d「model-first harness」一致:Harness 是执行衬底,扩展而非竞争模型推理。

## 3. 目标与非目标

### 目标(单一)
只提升喂给模型的**原材料质量**。不动输出 caps、不动输出结构、不动阶段流水线。保持「效率与质量分离」,便于独立归因质量改善。

### 非目标(明确排除)
- caps 数字统一(三处 agent.md / 两个 skill 互相打架)—— 属后续「第 4 条」。
- `source_extract` 工具的彻底删除与工具注册移除 —— 属后续「第 3 条」。
- 任何 `src/modi_harness/` 改动。

## 4. 详细设计

### §1 `fetch_url` 新职责
- **删除**内部对 `_select_evidence_facts` 的调用;`fetch_url` 不再生成 `facts` / `evidence_card`。
- `_TextExtractor` 增强(全部无损):
  - 收集真正的 `<title>` 文本。
  - 额外跳过 `nav`、`header`、`footer`、`aside` 标签内容(在现有 `script/style/noscript` 基础上扩展)。
- 返回**清洗后的较完整正文**,固定单源上限 `_MAX_BODY_CHARS = 32000` 字符(≈8k token);超出按字节截断并置 `truncated=true`。**替换**现有 `_MAX_PREVIEW_CHARS=1800` 的头部切片逻辑。
- 返回字段精简为:`url`、`content_type`、`truncated`、`size_bytes`、`source_tokens_estimate`、`title`、`content`。

> 说明:给模型「全文」而非「某个相关段落」,比再加一层段落抽取更贴合模型为主体 —— 原计划的「相关段落抽取」(原第 2 条)就此被吸收,不单独做。

### §2 EVIDENCE 阶段:模型读 `content` 自己抽证据
- `agent.md` 的 EVIDENCE 段与 `source-evaluation` skill 中「调用 `source_extract`、传 `source_id/url/extraction_profile`」的指令(本就与实函数签名不符、是坏的)改为:**模型直接读 `fetch_url` 返回的 `content`,产出结构化证据草稿**。
- 证据草稿结构保持不变(`comparison_dimensions/claims/evidence/source_coverage/open_questions`)。
- 这是让「模型挑」成立的必要改动,无法只动 `fetch_url`。

### §3 `source_extract` 去留(本步边界)
- 本步**只解耦**:`fetch_url` 不再内部调用它;agent.md / skill 不再指示模型调用它。
- **不删除**函数、不移除工具注册。它变成「不在默认路径上的休眠工具」。
- 彻底删除留待后续「第 3 条」,避免本步牵动契约测试与多处禁用清单,把改动范围压到最小。

## 5. 受影响测试(地面真相)

运行 `uv run pytest tests/examples/` 的当前结果:**3 failed, 18 passed**(在已提交状态下即为红,非本次改动引入)。

- `test_research_assistant_prompt.py` 当前已红的 3 项,根因是测试用**字面中文字符串匹配** agent.md,而文本已漂移(如断言 `"每个 URL 最多调用一次 fetch_url"` vs 实际 `"用户给定的每个 URL 最多 fetch 一次"`)。改 agent.md 必须同步更新这些断言。
- `test_source_extract_returns_compact_evidence_card`:**保持不变、保持绿色**。它直接调用 `source_extract`(不经 `fetch_url`),而 `source_extract` 本步不改。
- 因 §2 改 `source-evaluation` 文案而失配的断言需同步更新,具体为 `test_source_evaluation_outputs_structured_evidence_draft` 中 `"emit only the tool call"`、`"pass only \`source_id\`, \`url\`, and \`extraction_profile\`"`、`"After \`source_extract\`, carry forward only extracted evidence"` 这几条针对旧 `source_extract` 指令的断言。
- **新增**离线单元测试(合成 HTML,不打网络):
  - `_TextExtractor` 能抓 `<title>`、能跳过 `nav/header/footer/aside`。
  - `fetch_url` 返回 fuller `content`、含 `title` 字段、超额时 `truncated=true`、不再返回 `facts`/`evidence_card`。

## 6. 验证策略(诚实边界)

- **离线可验**:`uv run pytest tests/examples/` 全绿,含新单元测试。这是本会话能保证的部分。
- **质量需实跑验**:简报「变好」必须用一次带 `MODI_MODEL_API_KEY` 的真实运行肉眼验收(`uv run python examples/research_assistant/run.py`)。本会话无 key,**不会声称质量已验证**;由用户提供 key 或本地实跑确认。

## 7. 风险

- 单源 32000 字符 × 3 源 ≈ 24k token 进入上下文。若用户传入更多源,总量线性增长,由模型上下文窗口兜底;`truncated` 标志可观测。后续若需要,再引入总预算动态分配(本步不做)。
- 脆性字面匹配测试:本步顺手把断言改为更稳的检查(仍保持可读),但不重写整套测试策略。
