# 成员 A 数据模型设计 V1

本设计为 PaperPilot Secure 建立项目隔离、来源可追溯、任务可恢复的数据结构，作为下一步 PostgreSQL 迁移的依据。按“明确需求 → 生成分 E-R 图 → 合并总 E-R 图 → 解决冲突 → 映射关系模式”的顺序完成，尚未建表或改变现有接口。

| 顺序 | 成果 | 阅读内容 |
| --- | --- | --- |
| 1 | [需求分析与分 E-R 图](01-requirements-and-local-er.md) | 需求来源、业务规则、四个局部视图及基数 |
| 2 | 初步合并总 E-R 图 | 合并思路保留在 [设计检查记录](05-design-review.md)；历史合并图已随旧版绘图移除 |
| 3 | [冲突消解与最终模型](03-resolved-model.md) | 命名/属性/结构冲突、最终总图、约束及事务设计 |
| 4 | [关系模式与数据字典](04-data-dictionary.md) | 24 张逻辑表、字段、键及外键、26 个 DataPort 方法的落点 |

图形采用 **Chen 式记法：矩形是实体、椭圆是属性、菱形是联系，连线的 1/N/M 表示最大联系基数**。主键属性加实下划线；依附实体（执行尝试、证据字段、结果来源、版本引用快照、幂等记录）的局部编号加虚下划线表示部分键，须与所属实体主键组合。外键一律画成联系（上传、发起、保存、采纳、基于），联系名互不重复，总图仅“保存”与“导入”一处交叉并以跨线弧标示。

已渲染并检查的可缩放 SVG 在 [diagrams-chen-clean/](diagrams-chen-clean/README.md)，无需安装绘图工具：

| 阶段 | 图像 |
| --- | --- |
| 四个分图 | [身份与项目](diagrams-chen-clean/v1-identity-project.svg)、[文献与来源](diagrams-chen-clean/v2-literature-source.svg)、[研究与报告](diagrams-chen-clean/v3-research-report.svg)、[运行控制与审计](diagrams-chen-clean/v4-control-audit.svg) |
| 概念总图 | [总图（18 个概念实体、28 个联系）](diagrams-chen-clean/total-er.svg) |

分图保留各局部视图的核心业务属性；总图是概念总览，不是 24 张表的字段级图。**需要字段、复合外键和 24 张表时，以 [数据字典](04-data-dictionary.md) 为准**；图上省略的可空审计维度、条件目标键和派生指针等例外见概念版 [README 的合并与省略说明](diagrams-chen-clean/README.md)。图例、属性覆盖和联系基数的阅读方式见 [图例与联系基数说明](06-chen-notation.md)。

完成检查见 [设计检查记录](05-design-review.md)。绘图源为 `scripts/render_chen_clean.py`（手工坐标布局内嵌），在仓库根目录执行 `python scripts/render_chen_clean.py --check` 可重新生成 5 张 SVG，并检查关系线交叉（仅放行总图标了 hop 的一处）、连线穿过其他形状、包围盒重叠和文字溢出。脚本只使用 Python 标准库。

已确定的模型规则来自 PRD、AB-1 和现有 Schema；A 对实现方式作出的设计选择均在冲突表标明。C 的模型维度/载荷、D 的实际数据库授权机制、正式认证与审计投递参数仍需协作确认，不能将本设计视为这些能力已经实现。

本次只完成关系模型设计。没有新增迁移、种子数据、正式 DataPort、数据库角色策略或业务代码；原有 Mock 和独立 PG 预验证保持原状。
