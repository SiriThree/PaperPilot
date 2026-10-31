# 成员 A 汇报展示提纲与代码素材

核对日期：2026-10-28。依据本地 `member-a@195db34`、GitHub [PR #1](https://github.com/SiriThree/PaperPilot/pull/1) 及仓库实现和验证记录。本文围绕新版 Chen 图组织汇报，完整工程记录见 [成员 A 工作汇报](member-a-report.md)。

## 汇报主线

**我负责 PaperPilot 的数据库设计与持久化实现，从需求分析出发，绘制分 E-R 图、合并总图并解决命名、属性和结构冲突，再将模型映射为 PostgreSQL 关系表，通过 Python 数据访问层接入业务系统。**

建议将时间分配为：ER 建模约 40%，冲突消解约 30%，SQL/Python 落地约 20%，验证与协作边界约 10%。以 6 页、约 6 分钟为例：

| 页 | 展示内容 | 要讲清的问题 |
| --- | --- | --- |
| 1 | 个人职责与成果链路，约 30 秒 | 需求分析 → 四个局部视图 → 合并与冲突消解 → 关系模式 → SQL/Python 实现 |
| 2 | 四张分 E-R 图，约 70 秒 | 为什么按身份、文献、研究、运行控制划分，各视图解决什么问题 |
| 3 | 新版总 E-R 图，约 70 秒 | 跨视图如何统一实体；概念联系如何映射为关系表 |
| 4 | 命名、属性、结构冲突各一例，约 90 秒 | 原有歧义是什么，如何处理，避免了什么数据问题 |
| 5 | SQL 与 Python，约 70 秒 | 模型规则如何由数据库约束和应用事务共同落实 |
| 6 | 验证结果与交付，约 30 秒 | 已有证据是什么，哪些内容仍需跨成员联合验收 |

## ER 图：主要设计工作

从 PRD、AB-1 和现有接口提炼了 **R01—R13 共 13 条需求**，识别实体、属性、联系与基数，并形成四个局部视图：

| 分图 | 可直接展示的新版文件 | 讲解重点 |
| --- | --- | --- |
| V1 身份与项目 | [v1-identity-project.svg](data-model/diagrams-chen-clean/v1-identity-project.svg) | 用户与项目 M:N；项目角色是“参与”联系的属性，同一用户在不同项目可以有不同角色 |
| V2 文献与来源 | [v2-literature-source.svg](data-model/diagrams-chen-clean/v2-literature-source.svg) | 项目导入论文，论文关联作者、主题和原文片段；片段可由不同模型向量化 |
| V3 研究与报告 | [v3-research-report.svg](data-model/diagrams-chen-clean/v3-research-report.svg) | 任务、结果、证据、报告及版本引用快照；保留来源追溯与版本历史 |
| V4 运行控制与审计 | [v4-control-audit.svg](data-model/diagrams-chen-clean/v4-control-audit.svg) | 任务执行尝试、幂等、业务事件和审计，支持恢复、重试和追踪 |
| 总图 | [total-er.svg](data-model/diagrams-chen-clean/total-er.svg) | 18 个概念实体、28 个联系；建议沿“项目 → 论文 → 片段 → 任务结果 → 报告版本”讲解 |

新版采用 Chen 记法：矩形表示实体，椭圆表示属性，菱形表示联系；实下划线是主键，虚下划线是部分键。`1/N/M` 表示最大基数；“每项目恰好一位负责人”等最小参与和跨行规则需另外说明。

新版图的改进也可以作为个人工作展示：

- 将成员、作者、主题、任务论文范围、片段向量等关联结构还原为概念联系，联系上的角色、作者顺序等画为联系属性，减少重复表达。
- 将外键语义画成“上传、发起、保存、采纳、基于”等联系；报告版本之间的“基于”为自联系。
- 区分实时结果来源与版本引用快照；统一 `TASK_RESULT`、`IDEMPOTENCY_RECORD` 等命名，补充主键、部分键和核心业务属性。
- 提供标准库 Python 绘图脚本，检查交叉、穿形、形状重叠和文字溢出；总图保留一处带跨线弧的交叉。

**概念模型与关系模型要分开解释**：总图有 18 个概念实体，数据字典有 **24 张逻辑表、176 个字段**。例如“用户—参与—项目”映射为 `project_members`，片段与模型的“向量化”映射为 `chunk_embeddings`；概念层的论文在实现层分为元数据与项目导入记录。不能把总图介绍成“24 个实体的字段级图”。

当前 5 张图已吸收冲突消解结论；历史初步合并图已移除。汇报“分图 → 合并 → 消解”的过程时，用 [合并对应表](data-model/02-merged-er.md) 和 [冲突记录](data-model/03-resolved-model.md) 说明，不将新版图冒充仍有歧义的原始分图。

## 冲突解决：推荐重点讲三个案例

这里的冲突指多个局部视图合并时出现的**数据模型语义冲突**。本地另有一次同步 `origin/main` 的合并提交，但不能据此声称发生并手工解决了 Git 文本冲突。

| 类别 | 原来的问题 | 我的处理 | 解决的实际问题 |
| --- | --- | --- | --- |
| 命名冲突：论文编号（C01） | “论文编号”既可能表示书目元数据，也可能表示某项目的一次导入 | 对外 `paper_id` 固定为项目导入身份；内部书目使用 `metadata_id` | 同一文件导入不同项目时拥有不同公开身份，避免混淆资源归属 |
| 属性冲突：负责人和角色（C03） | `projects.owner_id` 与成员的 `role='owner'` 重复表达负责人，可能不一致 | 概念图将角色放在“参与”联系上；关系实现用部分唯一索引与可延迟复合外键保证一致 | 项目恰好一名负责人，且负责人必须是该项目成员 |
| 结构冲突：当前来源与历史引用（C06） | 如果当前结果与历史报告共用一套动态来源，重解析论文可能使历史报告发生变化 | 分离 `task_results/result_sources` 与 `report_versions/report_version_sources`，保存版本时冻结引用值 | 当前研究结果可以更新，已保存报告的正文与引用快照仍可追溯 |

讲法可以统一为：“发现什么歧义 → 比较身份、作用域或生命周期 → 决定合并/拆分/改名 → 用键约束和事务落实”。

完整设计记录有 **C01—C15 共 15 项**，其他可用于答辩的案例：

| 编号 | 类别 | 处理结论 |
| --- | --- | --- |
| C02 | 结构 | 每次导入独占元数据，与项目导入记录一对一；当前不跨项目共享内容 |
| C04 | 命名/结构 | `Chunk` 是稳定原文片段，`Source` 是上下文来源视图；引用编号不是全局主键 |
| C05 | 属性 | 引用编号按任务及 scope 唯一；证据不同字段可以重复使用相同编号 |
| C07 | 属性 | 未知年份为 NULL，处理状态、移出时间和索引状态分别保存；当前解析有独立指针 |
| C08 | 结构 | 任务与执行尝试分开；同一任务重试递增 attempts，拒绝旧尝试的迟到结果 |
| C09 | 结构 | 按结果类型派生 result_ref，避免设计一个指向多张表的伪外键 |
| C10 | 属性 | 幂等键包含用户、项目、操作和 key；request_id 用于追踪，不替代幂等键 |
| C11 | 结构 | 历史操作者关联 users，避免撤销成员时删除任务、报告或审计历史 |
| C12 | 属性/结构 | 分离模型登记与片段向量，记录模型版本和维度，不将演示 3 维写死为正式约定 |
| C13 | 结构 | 审计事实只追加，outbox 投递状态独立可更新，按 event_id 去重 |
| C14 | 结构 | 多对多展开为关联表，以含 project_id 的复合外键约束同项目归属 |
| C15 | 命名/权限 | 区分业务用户/项目角色与 PostgreSQL 登录角色，正式数据库授权需联合验收 |

## SQL 素材：优先选择能对应 ER 设计的代码

### 推荐片段一：负责人必须是成员，并且唯一

以下为 [001_initial.sql](../backend/app/data/migrations/001_initial.sql) 的实际片段，已省略中间的空行：

```sql
CREATE UNIQUE INDEX one_owner_per_project ON project_members (project_id) WHERE role = 'owner';

ALTER TABLE projects ADD CONSTRAINT projects_owner_is_member
    FOREIGN KEY (project_id, owner_id, owner_role)
    REFERENCES project_members (project_id, user_id, role)
    DEFERRABLE INITIALLY DEFERRED;
```

讲解：唯一索引保证每个项目**至多一名** owner；`projects` 中非空的负责人字段、固定为 owner 的 `owner_role` 配合复合外键，保证指定负责人**确实是该项目的 owner 成员**，共同实现“恰好一位”。延迟检查允许同一事务先插项目、再插负责人成员，提交时检查最终一致性。只展示唯一索引时，不能说它单独保证“恰好一位”。

### 可选片段二：作者关系与项目边界

以下为同一迁移文件中的完整建表语句：

```sql
CREATE TABLE paper_authors (
    paper_id uuid NOT NULL,
    author_id uuid NOT NULL,
    project_id uuid NOT NULL,
    author_order integer NOT NULL CHECK (author_order >= 1),
    PRIMARY KEY (paper_id, author_id),
    UNIQUE (paper_id, author_order),
    FOREIGN KEY (project_id, paper_id) REFERENCES project_papers (project_id, paper_id),
    FOREIGN KEY (project_id, author_id) REFERENCES authors (project_id, author_id)
);
```

讲解：M:N 联系转为关联表；复合主键避免同一作者重复关联；唯一键保证同一论文的作者顺序不重复；两个复合外键要求论文与作者属于同一项目。复合外键约束关联正确性，读取授权还需数据层校验与后续数据库权限设计。

## 高级语言素材：Python 数据访问与事务

实现语言是 **Python**，驱动为 **psycopg3**。正式 `PostgresData` 实现 AB-1 的 **26 个 DataPort 方法**，通过 `DataBinding` 接入 B 的业务层；SQL 负责关系约束，Python 负责输入/权限检查、事务编排、类型映射和错误语义。

推荐粘贴 [adapter.py](../backend/app/data/adapter.py) 中完整的事务装饰器：

```python
def operation(write=False):
    def decorate(method):
        @wraps(method)
        def invoke(self, *args, **kwargs):
            with self.database.transaction(write=write) as conn:
                token = self._connection.set(conn)
                try:
                    result = method(self, *args, **kwargs)
                    if write and self._before_commit is not None:
                        self._before_commit(method.__name__, conn)
                    return result
                finally:
                    self._connection.reset(token)

        return invoke

    return decorate
```

讲解：写方法使用 `@operation(write=True)`，每次调用独占一个事务，方法内部的 SQL 共用连接；事务上下文负责提交、回滚及错误映射。`_before_commit` 是测试注入点，用于验证真实提交前故障能否回滚，不是业务步骤。外部 AI 调用放在数据库事务之外。

用业务例子解释效果：

- `create_project`：项目、owner 成员和 outbox 事件一起提交，对应前述负责人循环外键。
- `import_paper`：元数据、项目导入记录、处理任务、任务范围、幂等响应和 outbox 一起提交，避免半条导入。
- `save_report`：报告版本、正文、引用快照、当前版本指针、幂等响应和 outbox 一起提交，并检查基础版本冲突。

若展示时间充足，再展示 `claim_job` 中的 `FOR UPDATE SKIP LOCKED` 和递增 attempts：多个 worker 领取不同任务，租约过期后创建新尝试；完成时核验尝试号，防止迟到结果覆盖。它属于工程加分项，不必挤占 ER 与冲突消解的主线。

## 其他已完成工作与验证证据

除建模与实现外，A 还完成 PostgreSQL 16 + pgvector 环境预验证、迁移与 readiness、真实库业务联调、事务性 outbox 投递器、性能基线和备份恢复实验。

下表为 **2026-10-27 已记录的验收结果**，本次整理没有重新运行数据库测试：

| 验证 | 已记录结果 | 证据 |
| --- | --- | --- |
| 共享契约及真实库门槛 | 41/41，含 6 个实际数据库门槛 | [实现记录](data-adapter.md) |
| K01—K15 及 C13 约束正反例 | 39/39 | [机器可读结果](day2-constraint-verification.json) |
| B 路由与 worker 在真实库上的联调 | 9/9 | [联调测试](../backend/tests/test_persistent_integration.py) 与实现记录 |
| outbox 投递器 | 5/5 | [投递器测试](../backend/tests/test_outbox_delivery.py) 与实现记录 |
| 备份恢复 | 7/7，真实 pg_dump/pg_restore 后核对业务状态与重放 | [机器可读结果](backup-restore-verification.json) |
| 性能实验 | 200 项目、4 千论文、3.2 万片段、10 万审计的合成规模；三个候选索引无计划级收益，未加入迁移 | [性能基线](performance-baseline.md) |

性能实验适合作为结尾补充：“我根据 A/B 计时和 EXPLAIN 决定是否增加索引；当前实验没有支持新增候选索引，审计深分页问题另列为游标分页改进。”不要把单机合成实验讲成生产容量承诺。

测试还发现并修复四个实现缺陷：重复启动时迁移误拒绝、并发导入错误码不准确、outbox 触发器静默取消更新、报告延迟触发器读取过期行值。详见 [已有工作汇报](member-a-report.md)。

## 当前状态和答辩口径

截至本次核对，本地与远端 `member-a` 均为 `195db34`；[PR #1](https://github.com/SiriThree/PaperPilot/pull/1) 为 **open / draft，尚未合并**，GitHub 当时显示 `mergeable=true`。该状态是生成本文前的快照，不代表本文后续本地提交已推送。

现有交付支持汇报“已完成独立数据层设计、实现和所列开发验收”。正式认证/会话、正式库 RLS 与受限角色、C 的真实向量提交面、租约心跳及幂等/outbox 保留策略仍属联合对接项。day0 的 RLS 预验证与正式库授权验收应分开；现有结构不能替代正式安全验收。

可以直接使用的收尾讲稿：

> 我主要负责数据库概念设计、关系模式设计和持久化实现。首先从需求中划分身份、文献、研究和运行控制四个视图，绘制分 E-R 图，再统一实体、联系和命名，形成总图。在合并过程中，我重点处理了论文编号的命名冲突、负责人角色的属性冲突，以及实时来源与历史报告快照的结构冲突。随后将概念模型映射为 24 张逻辑表，使用 SQL 的主外键、唯一约束和延迟检查落实规则，再用 Python 实现 26 个数据访问方法和事务编排。已有真实数据库约束测试、业务联调和备份恢复记录支撑这些设计；跨成员的正式认证、授权和真实向量对接仍需联合验收。
