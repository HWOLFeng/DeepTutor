# Three conversations this skill is built around

Each one is short on purpose. The long part of a planning turn is the card, not
the prose.

---

## 1. Generate — "线代期中还有一周，帮我排一下"

> **Learner**: 线代期中还有一周，帮我排一下。
>
> **Assistant**: 两个问题就够：期中具体哪天？每天大概能投入多少时间？
>
> **Learner**: 11/07，工作日一小时左右，周末可以多些。
>
> **Assistant** *(calls `plan_week` with goal, the week, and 4–5 proposed tasks)*
>
> 排好了，四份草稿里的三条任务加两个时间块，合计大约 3 小时——比你的一小时/天
> 留了余量，因为第 3 章那块内容比看起来重。周三晚上你已经有组会，所以那一块挪到了
> 周四下午。有两件事要你定：`错题重做` 和看板上已有的同名任务会合并，另外 `预习
> 第 4 章` 被标了与现有日程重叠。勾完点确认写入就行，我这边不会再动。

*What not to do here:* listing the four rows again in prose, or adding "要不要我直接
帮你建好？" — the card already asks.

---

## 2. Converge — "今天该干嘛？"

> **Learner**: 今天该干嘛？
>
> **Assistant**: 今天有三件：`第 3 章 特征值` 的时间块在晚上，`到期复习 12 个`
> 大概四十分钟，另外 `错题重做` 昨天没做完。如果只做一件，做复习——它明天会翻倍。
> 要我把 `错题重做` 挪到明天吗？

*Why this shape:* the planner context already answered the question. No tool call
was needed to read it, and a move is offered rather than performed — the learner
has not said yes yet.

---

## 3. Repair — "这周又没做完"

> **Learner**: 这周又没做完，全堆着了。
>
> **Assistant**: 五件逾期，全塞进今天只会重演一遍。三选一，我们一件件来：
> 顺延到下周（适合还来得及的）、降级成一次轻量复习（适合已经看过一遍的）、
> 这周直接放弃（我会把它从计划里去掉，不留在列表里碍眼）。
>
> **Learner**: 前两件顺延，剩下的降级。
>
> **Assistant**: *(proposes a revised `plan_week` draft)* 三件顺延、两件降级，
> 合计压到大约一个半小时。新的草稿在下面——旧的已经被这份取代了，不用管它。

*The trap this avoids:* applying the repair yourself because the learner sounded
tired. Tired is the moment a plan should be *smaller*, not the moment it should
be written without them.

---

## Phrases that mean "plan" and phrases that do not

| Means plan | Means something else |
| --- | --- |
| 帮我排一下 / plan my week | 我的日历上有什么 → **query**, no writes |
| 复习怎么安排 | 这题怎么做 → answer, no plan |
| 我落后了 / I'm behind | 我不想学了 → acknowledge, do not schedule |
| 明天要做什么 | 提醒我明天七点 → that is the `cron` tool, not a plan |
