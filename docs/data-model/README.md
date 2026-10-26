# 成员 A 数据模型设计 V1

本设计为 PaperPilot Secure 建立项目隔离、来源可追溯、任务可恢复的数据结构，作为下一步 PostgreSQL 迁移的依据。按“明确需求 → 生成分 E-R 图 → 合并总 E-R 图 → 解决冲突 → 映射关系模式”的顺序完成，尚未建表或改变现有接口。

| 顺序 | 成果 | 阅读内容 |
| --- | --- | --- |
| 1 | [需求分析与分 E-R 图](01-requirements-and-local-er.md) | 需求来源、业务规则、四个局部视图及基数 |
| 2 | [初步合并总 E-R 图](02-merged-er.md) | 合并对应表、尚未消解冲突的概念总图 |
| 3 | [冲突消解与最终模型](03-resolved-model.md) | 命名/属性/结构冲突、最终总图、约束及事务设计 |
| 4 | [关系模式与数据字典](04-data-dictionary.md) | 24 张逻辑表、字段、键及外键、26 个 DataPort 方法的落点 |

概念阶段使用业务实体和联系；关系模式阶段将多对多联系、结果来源及运行控制拆成逻辑表。分图及总图采用 Mermaid E-R 记法：`||` 为恰好一个，`o|` 为零或一个，`o{` 为零到多个，`|{` 为一个到多个。属性列于分图后的清单和数据字典；Mermaid 代码可在支持 Mermaid 的 Markdown 阅读器或 GitHub 中显示。

也提供已经渲染并检查的可缩放 SVG，直接打开即可查看，无需安装 Mermaid：

| 阶段 | 图像 |
| --- | --- |
| 四个分图 | [身份与项目](diagrams/v1-identity-project.svg)、[文献与来源](diagrams/v2-literature-source.svg)、[研究与报告](diagrams/v3-research-report.svg)、[运行控制与审计](diagrams/v4-control-audit.svg) |
| 合并阶段 | [初步合并总图](diagrams/merged-er.svg) |
| 冲突消解后 | [最终总图](diagrams/resolved-er.svg) |

完成检查见 [设计检查记录](05-design-review.md)。

已确定的模型规则来自 PRD、AB-1 和现有 Schema；A 对实现方式作出的设计选择均在冲突表标明。C 的模型维度/载荷、D 的实际数据库授权机制、正式认证与审计投递参数仍需协作确认，不能将本设计视为这些能力已经实现。

本次只完成关系模型设计。没有新增迁移、种子数据、正式 DataPort、数据库角色策略或业务代码；原有 Mock 和独立 PG 预验证保持原状。
