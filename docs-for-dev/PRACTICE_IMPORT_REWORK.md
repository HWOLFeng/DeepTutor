# 练习导入改造记录（2026-10）

[Handbook](README.md) · [Practice](PRACTICE.md) · [Fork handoff](FORK_MIGRATION.md)

本文记录 2026 年 10 月这轮「题库 / 错题导入」改造：起因、设计取舍、用户可见的
行为变化、验证证据，以及仍然存在的限制。产品用法见 [Practice](PRACTICE.md)；
本文件面向维护者，回答「为什么这样实现」。

## 1. 起因：三张真实照片暴露的问题

用三张小学作业照片（语文填空、混合运算、正方体展开图）跑导入，基线（改造前）：

| 指标 | 基线结果 |
| --- | --- |
| 识别题目 | 28 条 |
| 题干里带着答案 | 3 条（手写填空被并进题干） |
| 答对了却标成错题 | 10 条（`user_answer == correct_answer` 仍记 `incorrect`） |
| 同一页重复/矛盾的题 | 8 条（同一张展开图给出 4 种不同答案） |
| 题图裁到题干文字 | 有；另有一张裁掉一个面导致题目不可解 |

结论：问题不在「提示词少一句」，而在**整条链路没有验收**——生成之后只有格式校验，
没有检查「题干里有没有答案」「这题到底错没错」「图框是不是题目要的那张图」。

## 2. 设计原则

1. **模型负责把纸面信息转成结构，代码负责在结构上推理。** 能计算的答案不交给模型
   二次判断（两次模型意见一致是相关性，不是证据）。
2. **证据优先，但目录是指令。** 学生答案与正确答案可比较时按证据判定对错；无法比较
   时尊重用户选择的「错题 / 题库」。
3. **失败要可见。** 不确定的答案标「答案由模型求解，请核对」；两遍不一致标「答案待核对」；
   同一图上的重复题合并并显示数量。
4. **一图一题。** 重复判定的键是「题干指纹 + 图框」，题干相同但图不同不算重复。
5. **用户可撤销。** 预览列出全部识别结果，默认全选，可逐条取消。

## 3. 用户可见的行为变化

| 行为 | 现在 |
| --- | --- |
| 导入预览 | 列出**全部**识别结果，逐条勾选（默认全选），显示学生作答、正确答案与标记 |
| 错题判定 | 学生答案与正确答案按同一规则比较；答对的不计入错题（仍可复习） |
| 空位 | 手写填掉的空位还原为「（　　）」「＿＿＿＿」，手写内容进 `user_answer` |
| 公式 | 统一 LaTeX（`$...$`） |
| 判断题 | `√/×/对/错/T/F` 归一化为同一答案，`×，订正：…` 也能判对 |
| 新导入的题 | **当天**进入「今日复习」；练习中产生的新错题仍是次日 |
| 导入进度 | 后台任务 + 进度条与阶段文字，刷新页面后仍可读 |
| 侧栏 | 导入/复习完成后「学习记录」即时更新，不用刷新 |
| 分组练习 | 从侧栏点导入文件直接开练，页面可滚动（此前按钮会被裁掉） |

## 4. 实现位置

| 文件 | 作用 |
| --- | --- |
| `deeptutor/services/practice/quality.py` | 答案归一化、错题判定（correct/incorrect/unknown）、空位还原、重复题指纹 |
| `deeptutor/services/practice/verify.py` | 确定性校验：算式求值、等式链真值、选择题结构、正方体展开图折叠；`register_verifier()` 扩展新学科 |
| `deeptutor/services/practice/document_import.py` | 提示词契约（印刷/手写/批改分离、LaTeX、图框、`figure_data`）、第二遍答案核对、按图去重、进度回调 |
| `deeptutor/services/practice/storage.py` | 导入任务表 `practice_import_jobs`、按证据落库、导入当天到期、`_DUE_AT_SQL` 显式状态优先 |
| `deeptutor/api/routers/practice.py` | `POST /import/jobs` + `GET /import/jobs/{id}`、预览返回全量题目、提交支持 `selected` |
| `web/components/learning/practice/PracticeImport.tsx` | 勾选、进度条、标记与「答案由模型求解」提示 |
| `web/lib/learning-events.ts` + `web/hooks/useLearningIndex.ts` | 导入/复习后通知侧栏刷新 |
| `scripts/eval_practice_ocr.py` | 真实照片评测：题数/答案/泄漏/重复/图形 |
| `scripts/clean_practice_import.py` | 按批次清理导入（备份、题图、孤立附件） |
| `scripts/setup_chromium.sh` | 一次性下载 UI 测试用 Chromium 到 `data/.tools/`（不入 git） |

## 5. 验证证据

- 三图评测（`scripts/eval_practice_ocr.py`，同一批照片）：

  | 指标 | 改造前 | 改造后 |
  | --- | --- | --- |
  | 题干带答案 | 3 | **0** |
  | 答对却标错 | 10 | 0（改判 `correct` 或 `unknown`） |
  | 第 1、2 页 | 题干被手写污染 | 17 题全部结构正确，13 题可判对、4 题无法比较 |
  | 耗时 | 43s | 47–107s（多一次答案复核） |

- 真实导入（用户三图，20 题）：12 题判为答对、8 题判为错题；错题总数 10，今日复习 39
  （19 道旧题 + 20 道新题）。
- 回归：`tests/api/test_practice.py` + `tests/services/practice/` + workspace 复习用例
  共 112 项通过；前端 `practice-workflow` / `practice-group` / `learning-dashboard` 12 项通过；
  改动文件 `ruff` 与 `tsc --noEmit` 干净。
- 浏览器实测：导入后侧栏免刷新出现新文件；分组练习展开答案后按钮可达并可点击。

## 6. 数据与运维

- 新增表 `practice_import_jobs`（进度/结果），随 `initialize_practice()` 增量建表。
- 清理导错的批次：

  ```bash
  python scripts/clean_practice_import.py --db <chat_history.db> --material-id import:<token>
  python scripts/clean_practice_import.py --db <chat_history.db> --material-id ... --purge-files --apply
  python scripts/clean_practice_import.py --db <chat_history.db> --prune-orphans --apply
  ```

  默认只试运行；`--apply` 前自动备份数据库；`--purge-files` 删除题图，`--prune-orphans`
  清理未确认预览留下的孤立附件。
- UI 浏览器只下载一次：`scripts/setup_chromium.sh`，放在被 git 忽略的 `data/.tools/`，
  容器重建后直接复用；系统库在启动校验失败时自动补。

## 7. 已知限制与下一步

1. **展开图页面仍不稳定**：同一页多次运行会得到 20–50 行，偶尔同题重出。求解器已实现
   （`verify_net`），但依赖模型返回 `figure_data`，目前命中率不稳定；下一步是提高该字段
   的返回率，或在同图多题时按「一图一题」强制归并。
2. **答案复核用的是同一个模型**：两次一致并不等于正确，所以只做「标出分歧」，不做
   「自动采信」。可计算的题（算式、等式链、选择题、展开图）已由代码判定，覆盖不到的
   应用题仍标「请核对」。
3. **预览只能勾选，不能逐行编辑**：下一步加行内编辑（题干/答案/解析/换图）。
4. **导入仍是「上传即识别」**：进度条有了，但没有断点续跑；单页模型调用超时会整页报错。
5. 评测集在 `data/eval/practice-ocr/raw/`，`data/` 不入库，换机器需自行放入照片。
