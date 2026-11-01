# 题组（Practice Set）与「举一反三」

[手册](README.md) · [练习总览](PRACTICE.md) · [导入改造](PRACTICE_IMPORT_REWORK.md)

学习侧栏的「学习记录」把题库按**批**列出来：一个导入文件是一批，一次生成的
测验是一批，一个伙伴记录下来的错题是一批。这一批就是**题组**（practice set），
它是学习者能一口气做完的东西，也是侧栏那一行的全部含义。

本文规定题组的两件事——**它包含哪些题**（身份）和**它叫什么**（名称）——以及
「举一反三」生成新题组时的产品契约。两者分开，是这个文档存在的原因。

## 要解决的问题

| 现象 | 根因 |
| --- | --- |
| 侧栏出现多行同名题组（例如都是 `Partner notebook`），或本应分开的两组被合成一行 | 旧实现按 `material_title`（显示名）分组——**名称被当成了键** |
| 会话里生成的这组题，点侧栏那一行没有反应，或跳到练习首页；导入的文件却正常 | 会话题组没有可寻址的身份，行里没有可打开的集合 |
| 伙伴记录的错题永远叫 `Partner notebook` | 命名是固定字符串，不理解这批题是什么 |

## 身份：名称永远不是键

一条记录归入哪个题组，由下列表达式决定（
[notebook_sets.py](../deeptutor/services/session/notebook_sets.py) 的
`GROUP_KEY_SQL`，侧栏分组与题库筛选共用同一段 SQL）：

| kind | 键 | 谁属于它 |
| --- | --- | --- |
| `material` | `material\|<material_id>` | 显式材料：导入文件 `import:<token>`、生成的测验 `quiz:<turn_id>`、伙伴配对 `partner:<pid>:<actor>`、命名的变式组 `set:<hash>` |
| `origin` | `origin\|<origin_ref>` | 没有材料 id 的外部写入（旧版伙伴记录、旧版导入） |
| `turn` | `turn\|<session_id>\|<turn_id>` | 早期版本在会话里生成的测验（升级前的存量数据） |
| `other` | `other` | 精通之路、书籍、沉浸阅读的题目：它们有自己的侧栏行，不重复出现在练习里 |

只有 `import` / `deep_question` / `partner_chat` 三种来源会形成练习题组；
其余来源固定在 `other` 汇总行，标签统一显示为「其他题目」。

客户端拿到的 `group_ref` 是 `g:` 前缀加键，例如 `g:material|quiz:turn_A`。
`g:` 前缀让新旧链接可以共存：没有前缀的 `?group=<material_id>` 仍然被理解为
材料 id（旧书签、旧侧栏链接、旧截图里的 URL 继续有效）。

`other` 汇总行不写字面名称：它混合了不同页面产生的题目，用其中任意一条的标题
命名都会误导，客户端改为显示「其他题目」。

## 名称：先看来源，再看上下文，最后看内容

`set_title()` 的优先级：

1. 题组自身的名字（`material_title`）：文件名、伙伴名、生成测验的主题；
2. 生成它的那段会话标题（`New conversation` 这种占位标题会被跳过）；
3. 组内第一条题目的题干（截断 120 字）。

**名称不拼时间戳**。侧栏每一行右边已经有时间，把日期写进名称会让同一组题在不同
日子显示成不同的名字；时间信息应该出现在时间的位置。

同一名称出现在两行是允许的，因为身份不同：两位同名伙伴、同一文件的两次导入、
同一会话里的两轮生成，都是各自独立的题组。

## 链接协议

侧栏行为以下形式，三种来源完全一致：

| 情况 | 链接 | 行为 |
| --- | --- | --- |
| 题组只有 1 题 | `/learning/practice?question=<id>` | 直接打开那道题 |
| 题组有多题 | `/learning/practice?group=<group_ref>&file=<title>` | 直接开始整组练习，标题显示 `<title>` |
| `other` 汇总行 | 同上（`group=g:other`） | 按组列出汇总题目 |
| 旧链接 | `/learning/practice?group=<material_id>` | 等价于 `g:material|<material_id>` |

题库筛选用同一个键：`GET /api/question-notebook/entries?group=<group_ref>`。
这条接口保证「行上写几题，点开就是几题」——侧栏分组和题库筛选是同一个表达式，
回归测试 [test_notebook_sets.py](../tests/services/session/test_notebook_sets.py)
逐行校验这一点。

## 各写入方如何产生题组

| 来源 | material_id | material_title | 说明 |
| --- | --- | --- | --- |
| 练习导入 | `import:<token>` | 文件名 / 图片组名 | 一次导入一个 token，重复导入不同文件不会合并 |
| 会话中的测验 | `quiz:<turn_id>` | 生成时的主题（见下） | `POST /sessions/{id}/quiz-results` 写入 |
| 伙伴记录错题 | `partner:<pid>:<actor>` | 伙伴显示名 | 一个伙伴配对一组，家族可整组复习 |
| 产品会话里记录 | `chat:<actor>:<本地日期>` | `Mistakes · <日期>` | 没有伙伴配对时的诚实归属：按天成组 |
| 举一反三（agent 生成） | `set:<hash>` | agent 给定的组名 | `question_bank` 工具的 `record_set` |
| 存量数据 | 无 | 会话标题 | 落在 `turn` 桶，仍可整组打开 |

### 生成测验的组名从哪来

`QuestionPipeline` 在结果信封里给出 `summary.title`：取 planner 为这套题选的
主题（最多 3 个去重，中文用「、」连接，其它语言用「; 」）。前端
`extractQuizSetTitle()` 读它，答题上报时随 `set_title` 一起提交。这样组名描述的
是「这套题在练什么」，而不是「某年某月某段对话」。

上报时若 `set_title` 为空，服务端保留已存的名字（重试不会把名字抹掉）。
学完第一题就会写题库，因此**没答完的测验也已经是完整的组**，不会掉进汇总桶。

## 「举一反三」产品契约

**目标**：学习者从一次会话里挑出几道题（种子），得到一组同知识点、同题型、
难度递进的新题，作为可练习的题组出现在学习侧栏。

### 输入

- **种子**：来自会话里的测验卡片、题库或错题本。应用入口最多 5 道（勾选超过即置灰），
  后端接受最多 10 道。种子必须带题干；答案、解析、难度、知识点标签有则使用。
- **数量**：默认与种子数量相同；要求更多时按种子循环（单道种子也能出 3 道），
  上限 30（与 `record_set` 一致）。
- **难度**：不指定时按种子难度起步、后三分之一升一档（易→易→中，中→中→难，
  难封顶）；显式指定则整批使用该难度。

### 生成规则

1. **同一个知识点，不同的题面**：数字、情境、提问方式必须变；不得照抄种子的
   题干、选项或答案。
2. **保持题型**：选择题的选项个数与正确项位置不固定；填空题只有一个空；
   判断题是单一命题。
3. **难度阶梯**：默认前三分之二与种子同档、其余升一档（不降档、不越级），
   显式指定的难度整批生效。
4. **可判定**：每题都必须给出可判定的 `correct_answer`；算得出来的题不给
   「由模型求解」的答案。
5. **不重复**：先看种子的同组题目与既有题组（`question_bank` 的 `list`），
   避免与已有题目逐字重复。
6. **附解析**：每题的解析说明为什么，而不是只给答案。

### 命名规则

组名要说清「练什么」，必要时带日期：

- 好：`二次函数 · 变式 5 题`、`投影的几何意义 · 变式`、`分数四则 · 2026-11-01`
- 差：`举一反三`、`新题`、`练习`、`Partner notebook`

组名是标签，不是键：同名两次是两组，各自独立。`record_set` 在缺名时直接拒绝，
把命名规则落在工具边界上，而不是靠提示词自觉。

### 落库与验收

`question_bank(action="record_set", set=<组名>, questions=[...])`：

- 所有题写入 `notebook_entries`，`result="ungraded"`、`is_correct=0`——
  未作答的变式题**不会**进入错题集合或复习队列；
- 题组身份 `set:<hash(origin, 组名)>`：同一批重跑是更新，不同名是另一组；
  写入时用该身份作 `turn_id`（存储层唯一的"批次"维度），所以同一道题可以同时
  属于两个命名不同的题组，而不会互相覆盖；
- 返回文本告诉 agent 组已保存、学习者在哪里能看到，并要求 agent 用一句话说明
  「这组练什么、几题、从哪进入」。

学习者侧验收：学习侧栏出现一行该组名，右侧数字等于题数，点开直接从第一题开始；
题库的「材料」筛选里也能按组名找到同一批题。


### 入口与链路（第二期）

学习者在应用里有两个起点，走的是同一条链路：

| 入口 | 位置 | 种子的来源 |
| --- | --- | --- |
| 「相似题」（`Similar questions`） | 题库 / 练习页的多选浮动条 | 勾选的 1–5 道题 |
| 「再来几道类似的」（卡片右上角 ✨） | 会话里测验卡片的题头 | 当前这道题 |

1. 入口把选中题目映射成种子（[quiz-seeds.ts](../web/lib/quiz-seeds.ts)）：
   题干、题型（题库的 `single_choice`/`multi_choice`/`true_blank`… 归一到
   `choice`/`concept`/`fill_in_blank`/`short_answer`/`written`/`coding`）、
   选项、答案、解析、难度、知识点标签（优先取标签，其次材料名）。
2. 种子经一次性会话存储交接（[pending-quiz-seeds.ts](../web/lib/pending-quiz-seeds.ts)），
   同时把开场白预填到输入框（[variant-launch.ts](../web/lib/variant-launch.ts)），
   然后跳到 `/chat?capability=deep_question`。种子不进 URL：它带着答案与选项，
   放进查询串既会超长，也会把学习者的作答留在历史里。
3. 新会话的 composer 消费种子，写入 `deep_question` 配置
   （[ChatWorkspace.tsx](../web/features/chat/components/ChatWorkspace.tsx)）：
   配置卡显示「种子题（N）」列表，可逐条移除或整体清空；清空即回到按主题出题。
4. 发送时 `buildQuizWSConfig` 带上 `seed_questions`；后端
   [capability.py](../deeptutor/agents/question/capability.py) 把每道种子变成
   `QuizTemplate(source="variant")`，数量取 `max(请求数, 种子数)`（单道种子也能
   出 3 道，按种子循环），上限 30；随后复用普通测验的逐题生成、校验、流式渲染。
5. 生成结果 `mode="variants"`、`summary.title` 带本地化后缀（`· 变式`），
   学习者作答后按上一节的规则落成 `quiz:<turn_id>` 题组。

种子为空却声称「有种子」会直接报错（`variants_invalid_seeds`），不会静默退化成
按主题出题——那等于回答了另一个请求。


## 去重、知识点与下一组（第三期）

题组能开、能命名之后，剩下三件事让「举一反三」变成闭环：不要让同一道题反复出现、
记住每组在练什么、并让一次失败变成下一次更容易的一组。

### 近似重复不落库

`record_set` 在写入前做一次近似重复检查（[question_bank.py](../deeptutor/tools/question_bank.py)）：

- 指纹 = 题干指纹（忽略空格、标点、填空位）+ 选项集合（忽略顺序与大小写）；
- 只比较**本题组以外**的题目：同一批重跑是更新，不会把自己的题当成重复；
- 同一批内部的近似重复也会被跳过；
- 返回文本与 `summary.duplicates` 报告跳过了几道，全部重复时明确说「没有新增」。

判定口径刻意保守：题干相同但选项不同的选择题算两道题——选项本身就是题目，合并
会丢掉一道真题。

### 知识点回填

每道题带一个知识点标签（`section_title`）：

- 会话测验：`POST /sessions/{id}/quiz-results` 的每条答案可带 `topic`（卡片上的
  `concentration`），落库为 `section_title`；
- 变式题组：`record_set` 的每道题可带 `topic`。

于是同一个题组可以按知识点查看，后续统计也不必再从题干里猜。

### 校准：这组题到底练得怎么样

`GET /api/question-notebook/sets/calibration?group=<group_ref>`

| 字段 | 含义 |
| --- | --- |
| `count` / `answered` / `correct` | 组内题数、已作答数、答对数（已作答 = 有作答或有判定结果，未作答的生成题不计为错） |
| `accuracy` | `correct / answered` |
| `label` | `untouched`（还没作答）、`too_hard`（< 50%）、`on_track`、`too_easy`（≥ 90%） |
| `suggested_difficulty` | 下一组建议难度：太难看 `easy`，太容易看 `hard`，其余留空 |

`GET /api/question-notebook/sets/attention?limit=3&min_answered=3`
返回需要再练一组的题组（仅 `too_hard`，正确率由低到高）；少于 `min_answered`
次尝试的组不参与——三道题不构成一次测量。

前端两处使用：

- **题组页顶部**：正确率、答对数与「需要再练一组」标记，附一个按钮直接用本组题目
  开一组变式；太难的组把种子的难度下调一档（`withSeedDifficulty`），太容易的上调
  一档；
- **练习首页**：列出最多 3 个需要再练的题组，点进去落在题组页——提醒只负责指出
  哪一组，生成动作仍发生在题组页。

提醒是**每次读取时计算**的，没有新增调度器：练习首页刷新（进入页面、窗口聚焦、
每分钟轮询）就会重新评估，复习调度保持不变。

## 为什么要（也不只要）沉淀成 Skill

结论：**要沉淀，但 Skill 只承担判断与流程，身份与渲染必须留在代码里。**

- 代码负责（已经落地）：题组身份、命名优先级、链接协议、未作答不判错、
  同名不合并。这些是可验证的不变量，写在提示词里只会变成「模型可能会」。
- Skill 负责（`deeptutor/skills/builtin/practice-variants/SKILL.md`）：
  何时该做举一反三、如何从会话里确认种子、各学科的变式手法（同构、逆向、
  情境替换、边界值）、难度如何递进、组名怎么起、交付时怎么说明。
  这部分是判断，随学科和学段变化，适合用可读、可改、可分发的 skill 承载。

只写 Skill、不改代码的后果是「模型尽力而为」：组名可能重复、题组可能没有身份、
未作答的题可能被算成错题。只改代码、不写 Skill 的后果是「能力在了，但没人会用」：
agent 不知道该在什么时机、用什么手法生成变式。两者缺一不可。

## 研发分期

**第一期（本次已实现）**

- 题组身份与命名规则（`notebook_sets.py`），侧栏分组与题库筛选共用同一表达式；
- 会话测验写入 `quiz:<turn_id>` 与主题组名，未答完也是完整组；
- 伙伴记录按配对成组、按伙伴命名；产品会话内记录按天成组；
- `question_bank` 新增 `record_set`：agent 可以把新生成的题落成命名的题组；
- 内置 skill `practice-variants`：举一反三的流程、变式手法与命名规范；
- 回归测试覆盖：身份不等于名称、引用回环、旧链接兼容、未作答不计错。

**第二期（已实现）**

1. **种子入口**：题库多选浮动条的「相似题」与会话测验卡片的「再来几道类似的」，
   把选中题带进 composer 的 `deep_question` 配置（`seed_questions`），
   配置卡可见、可逐条移除。
2. **capability 变式模式**：`deep_question` 读取 `seed_questions`，跳过
   explore/plan，按种子构造 `source="variant"` 的模板，复用逐题生成 / 校验 /
   流式渲染；结果为 `mode="variants"`，组名带「· 变式」后缀。
3. **命名与落库打通**：变式批次作答后按 `quiz:<turn_id>` + 主题名成组，
   与普通测验完全一致（技能路径则用 `record_set`）。

**第三期（已实现）**

- **去重**：`record_set` 按「题干指纹 + 选项集合」跳过与题库其他题近似重复的题，
  并报告跳过数量（不误伤同题组重跑与同题干不同选项的题）；
- **知识点回填**：测验答案与变式题都带 `topic` → `section_title`，题组可按知识点看待；
- **变式表现回填**：题组校准接口给出正确率、`too_hard`/`on_track`/`too_easy` 判定
  与下一组建议难度；题组页据此一键开「更简单/更难」的下一组；
- **提醒**：练习首页列出需要再练的题组（每次读取时计算，不新增调度器）。

**第四期（待做）**

- 把「建议难度」直接带给 `deep_question` 的变式模式（目前通过下调种子难度实现，
  等价但不够显式）；
- 近似重复检测加入语义相似（现在只看题干与选项的确定性指纹）；
- 跨设备/邮件的「再练一组」推送（现在只有打开练习页时的提醒）。

## 验收清单

| 检查 | 期望 |
| --- | --- |
| 同一会话连做两轮测验 | 侧栏两行，各自数量正确，都能整组打开 |
| 两个同名伙伴各记录错题 | 两行同名，互不合并 |
| 同一文件导入两次 | 两行同名，各自的 token 独立 |
| 会话生成的题组 | 行名是主题，不是会话标题；点开直接开始 |
| 只答了 1 题的测验 | 侧栏已出现该组，数量为已答题数 |
| 举一反三生成的题组 | 未作答、不在错题集合、`result=ungraded` |
| 题库勾选 3 题 →「相似题」 | 跳到测验 composer，配置卡显示 3 个种子，发送后生成 3 道、组名带「· 变式」 |
| 测验卡片 ✨ | 以当前题为唯一种子，生成 3 道；可用在作答之前 |
| 勾选超过 5 题 | 「相似题」置灰并说明上限，不会静默截断 |
| 种子被清空后发送 | 回到按主题出题，不再声称变式 |
| 重复生成同一批题 | `record_set` 报告 `duplicates`，题库题数不变 |
| 题干相同、选项不同的两道题 | 两道都保存，不被当作重复 |
| 题组答对 1/4 | 题组页显示「正确率 25%，需要再练一组」，按钮生成更简单的一组 |
| 练习首页 | 最多列出 3 个正确率低于 50% 的题组，点进去是题组页 |
| 未作答的新题组 | 不显示校准条，也不进提醒列表 |
| 旧 `?group=<material_id>` 链接 | 仍然打开对应导入题组 |

## 实现索引

- 题组身份与命名：[notebook_sets.py](../deeptutor/services/session/notebook_sets.py)
- 侧栏分组与题库筛选：[sqlite_store.py](../deeptutor/services/session/sqlite_store.py)
  （`_list_notebook_navigation_sync`、`_question_bank_filters`）
- 测验组名：[pipeline.py](../deeptutor/agents/question/pipeline.py)（`_set_title`）；
  上报：[sessions.py](../deeptutor/api/routers/sessions.py)（`record_quiz_results`）
- 侧栏与链接：[learning-dashboard.ts](../web/lib/learning-dashboard.ts)、
  [LearningNavigation.tsx](../web/components/sidebar/LearningNavigation.tsx)、
  [PracticeGroupSession.tsx](../web/components/learning/practice/PracticeGroupSession.tsx)
- agent 写入：[question_bank.py](../deeptutor/tools/question_bank.py)（`record`、`record_set`、近似重复、知识点）
- 校准：
  [notebook_sets.py](../deeptutor/services/session/notebook_sets.py)（`calibration_payload`）、
  [sqlite_store.py](../deeptutor/services/session/sqlite_store.py)（`notebook_set_calibration`、`notebook_sets_needing_practice`）、
  [question_notebook.py](../deeptutor/api/routers/question_notebook.py)（`/sets/calibration`、`/sets/attention`）、
  [PracticeGroupSession.tsx](../web/components/learning/practice/PracticeGroupSession.tsx)、
  [ReviewHome.tsx](../web/components/learning/practice/ReviewHome.tsx)
- 变式模式（后端）：[capability.py](../deeptutor/agents/question/capability.py)（`_seed_questions`、`_run_variants`）、
  [pipeline.py](../deeptutor/agents/question/pipeline.py)（`source="variant"`、`is_variants`、组名后缀）、
  [prompts/en/pipeline.yaml](../deeptutor/agents/question/prompts/en/pipeline.yaml)、
  [prompts/zh/pipeline.yaml](../deeptutor/agents/question/prompts/zh/pipeline.yaml)
- 变式入口（前端）：[quiz-seeds.ts](../web/lib/quiz-seeds.ts)、
  [variant-launch.ts](../web/lib/variant-launch.ts)、
  [pending-quiz-seeds.ts](../web/lib/pending-quiz-seeds.ts)、
  [BankSelectionBar.tsx](../web/components/space/question-bank/BankSelectionBar.tsx)、
  [QuizViewer.tsx](../web/components/quiz/QuizViewer.tsx)、
  [QuizConfigPanel.tsx](../web/components/quiz/QuizConfigPanel.tsx)
- skill：[practice-variants/SKILL.md](../deeptutor/skills/builtin/practice-variants/SKILL.md)
- 回归：[test_notebook_sets.py](../tests/services/session/test_notebook_sets.py)、
  [test_question_bank_tool.py](../tests/tools/test_question_bank_tool.py)、
  [learning-dashboard.test.ts](../web/tests/learning-dashboard.test.ts)、
  [practice-group.spec.tsx](../web/tests/practice-group.spec.tsx)、
  [test_variants.py](../tests/agents/question/test_variants.py)、
  [quiz-seeds.test.ts](../web/tests/quiz-seeds.test.ts)、
  [variant-launch.spec.ts](../web/tests/variant-launch.spec.ts)、
  [quiz-config-seeds.spec.tsx](../web/tests/quiz-config-seeds.spec.tsx)、
  [practice-attention.spec.tsx](../web/tests/practice-attention.spec.tsx)、
  [practice-group.spec.tsx](../web/tests/practice-group.spec.tsx)
