# 成员 D：本机 HTTPS 生产安全链路验收

当前已继续接入 C 的真实文献 AI；最新版本、41 项安全检查、20 项文献检查及演示见 [C/D 联合 HTTPS 验收](member-c-d-https-acceptance-20261031.md)。下方保留此前统计模型阶段与首次模板部署记录。

## 统计模型接入后的当前部署

2026-10-31（Asia/Shanghai）经用户授权，将另一段对话的模型 SQL 接入同一个 <https://localhost:8443>。部署编号及数据库保持不变，未创建新的验收实例。统计模型为 `deepseek-flash`，C 文献处理 AI 仍明确禁用。

- 当前源码指纹：`ede6aff0b143e72e8e3f4edf03149eaf4bd8e4ab33a11d9ea353089d9449d86f`。
- 后端／前端镜像标签后缀：`ede6aff0b143`。
- 模型配置从用户指定的 `.runtime/local-model/model-config.json` 校验、保护并供应至凭据卷，不进入 Git、镜像、Compose 环境变量或报告。
- 当前 HTTPS 联合验收 **40 项通过**，包括真实模型三类查询、自然语言改写、危险请求拒绝、模型策略标识及原有安全检查。结果为 `.runtime/production/evidence/acceptance.json`。
- 补充核对 **3 类真实模型问题**：返回列正确，按请求编号关联的 prepared 审计 SQL 与对应可信统计计划完全一致。结果为 `.runtime/production/evidence/model-intents.json`。
- 浏览器 **1 项端到端通过**：正常证书验证、凭据登录、自然语言改写查询、模型 policy_id、真实结果、当前 40 项验收和模型状态可见；文献 AI 与生产 Docker 实验仍禁用。测试会在结束时注销会话。
- 相关隔离 PostgreSQL、模型接线、失败审计、恶意候选、生产配置、API 和契约回归：**206 passed / 0 skipped / 0 failed**，记录 `.runtime/production/model-integration.xml`。模型故障和恶意候选回归使用受控 HTTP 响应；真实模型调用的证据另列于上述 HTTPS 结果，不混计。
- Ruff、格式、OpenAPI、前端类型与构建通过。实际页面已截图核对，截图在受保护的 `.runtime/production/browser-artifacts/`。

原 35 项模板验收归档为 `.runtime/production/evidence/acceptance-template-20261031.json`。新版本不会沿用旧代码或不同模型配置的验收记录。当前验收绑定代码指纹与模型配置指纹；模型调用失败不回退为模板成功。

更新前的数据库备份及隔离恢复已通过：`.runtime/production/backups/20261031T083514Z-39e801d9`。更新后带模型配置的备份及隔离恢复也已通过：`.runtime/production/backups/20261031T084530Z-5936fe93`，证据为 `.runtime/production/evidence/backup-restore.json`，与上述当前指纹一致。隔离恢复不访问外部模型，只检查配置保留、安全边界与原生执行。恢复后再次确认 HTTPS 健康、40 项验收匹配、验收账号禁用，且 API Key 不出现在 Docker inspect 元数据中。

本次验证覆盖有限正常与拒绝问题，不宣称完整模型语义准确率、大规模性能或文献 AI 已验收。演示仍使用已供应项目读取身份的现有项目；新增项目自动供应尚未实现。未推送或修改远程 PR。

## 首次模板部署记录（历史）

以下为模型接入前的验收记录；其中版本、通过数量与“当前部署”描述均指首次模板部署当时的状态。用户最初确认目标为本机 Docker、HTTPS 正式配置，先验收安全链路，AI 明确不可用。

结论：该范围已部署并完成真实验收。访问 <https://localhost:8443>，账号 `owner`，密码在受保护的 `.runtime/production/owner-credentials.txt`。详细演示与运维命令见 [部署说明](production-security-deployment.md)。未推送或修改远程 PR。

## 被验收的部署

- 部署编号：`458fa5ff-8a69-49f2-be6e-104fc2a8476d`。
- 应用源码指纹：`65790751356df04aca5461f006e75d484ee55a05ed5e8e11572de2fed0b5f2e3`。
- 后端与前端镜像标签：`paperpilot-production-backend:65790751356d`、`paperpilot-production-web:65790751356d`。
- `ENV=production`，A=persistent、C=disabled、D=real，PostgreSQL 16，业务迁移第 8 版。
- 独立新卷、独立业务／认证／审计／outbox／项目读取身份，NOLOGIN 审计和凭据锁所有者。
- 后端以 UID 10001 运行，根文件系统只读、丢弃全部 capabilities；数据库与后端没有宿主端口，HTTPS 只绑定 `127.0.0.1:8443`。
- 已比对当前工作区、部署 manifest、运行镜像报告的指纹一致；当前服务只采纳匹配部署编号及该指纹的验收记录。

## 当前部署的真实验收

`scripts/production_deployment.py verify` 的 **35 项检查通过**，原始结果归档为 `.runtime/production/evidence/acceptance-template-20261031.json`。这些检查连接当时的 HTTPS 服务和生产数据库，包含：

| 项目 | 实际检查与结果 |
| --- | --- |
| HTTPS | 信任的本地 CA、正常证书验证、HSTS，通过 |
| 真实身份 | owner／成员／外部用户密码登录、错误密码拒绝、匿名拒绝，通过 |
| 会话与 CSRF | Secure、HttpOnly 会话，Secure CSRF Cookie，缺少令牌或外部 Origin 拒绝，通过 |
| 项目授权 | 外部用户无法读取项目，成员无法读取负责人审计，撤权后下一请求拒绝，通过 |
| 原生统计 | 三类固定模板均 `allowed / executed / mock=false`，不支持问题拒绝且不执行，通过 |
| 操作审计 | 当前数据库有实际问题、SQL、请求编号和执行结果，通过 |
| 数据库第二层 | 项目视图跨项目零行、底表读取拒绝、正常可写事务中的 UPDATE 被 ACL 拒绝，通过 |
| 业务降权 | 业务登录无法读取密码哈希／会话，不能更新审计，通过 |
| 审计失败关闭 | 暂时给予审计登录异常 SELECT 权限，查询拒绝并不返回数据；撤销后恢复，通过 |
| 持久化与注销 | 后端重启后原会话及审计保留，注销后原会话失效，通过 |
| 登录限流 | 连续错误登录出现 429，通过 |
| 明确禁用 | AI 返回 `503 AI_NOT_CONFIGURED`；生产站点启动 Docker 攻防实验被拒绝，通过 |

验收完成后，临时成员和外部账号已禁用，相关会话撤销，受控权限漂移已修复。`owner` 保留可用。

浏览器通过 **1 项真实端到端测试**：正常 TLS 校验、密码登录、进入安全样例项目、重新运行当前预检、执行真实统计、显示本次真实结果与匹配部署验收、区分历史证据，以及禁止生产 Docker 实验。测试不使用 `ignoreHTTPSErrors`。截图位于受保护的 `.runtime/production/browser-artifacts/`；测试源为 `frontend/production-tests/security.spec.ts`。

## 备份恢复

完成一次有效的数据库备份与隔离恢复：`.runtime/production/backups/20261031T065824Z-e95154ba`。无网络临时 PostgreSQL 中恢复数据库级 ACL、对象和角色，按角色名重新绑定 OID 后：

- 全部 public／paperpilot_security 表计数和迁移指纹一致。
- 认证隔离、业务降权、审计边界、outbox 权限预检通过。
- 恢复库中的真实统计查询与独立关键审计通过。
- 临时恢复容器清理，正式服务恢复运行。

原恢复点的 manifest 位于上述备份目录；`.runtime/production/evidence/backup-restore.json` 记录最新演练，应核对对应指纹。完整备份包含服务密码、角色验证信息和本地 CA 私钥，受本地 ACL 保护，不得上传。此前中断／失败的备份目录没有完整 manifest，不计为通过。

本演练证明安全数据库恢复，不证明旧环境数据迁移或已上传论文文件的恢复。当前 AI／上传入口明确禁用。

## 代码验证及范围

- 实现阶段隔离 PostgreSQL、真实 A/C/D 适配器回归：**1034 passed、35 skipped、0 failed**。其中 C 使用本地模型替身，属于代码回归；并非本次 production 的真实 AI 验收。该全量测试跨越了少量后续修复，不将其标记为最终指纹的冻结快照。
- 最终相关生产配置、认证边界、审计角色与启动门槛复测：**47 passed、0 skipped、0 failed**，记录 `.runtime/production/final-boundaries.xml`。
- 全量的 35 个跳过：31 个历史 SQLite、2 个 POSIX 文件权限、1 个真实模型启动 opt-in、1 个预构建隔离安全镜像 opt-in。
- Ruff、Python 格式检查、OpenAPI 契约、前端类型与构建通过。原有历史静态报告未改写为当前部署验收。

范围限制：未接入真实模型；未验收公网部署、大规模性能或全部业务底表原生 RLS。项目原生读取身份目前预先供应三个项目，新建项目统计请求会明确失败关闭，尚无自动供应流程。旧 `paperpilot-local_postgres` 与其他演示数据库保留，未覆盖。
