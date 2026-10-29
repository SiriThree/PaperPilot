# D：独立审计身份与服务登录生命周期

2026-10-28。本项只提供代码、管理命令和一次性 PostgreSQL 验证，未操作正式业务库。A 的迁移、DataPort 和公开 Schema 保持不变。

## 独立审计写入

`app.security.audit.compose_audit(config)` 创建独立连接池并复用 A PostgresData.append_audit。D 的关键事件通过 `build_security(..., audit_append=binding.append)` 写入；成员检查仍使用业务 DataPort，统计仍使用项目读取身份。

提交前检查依赖当前 A PostgresData 的 before_commit 钩子；升级 A 实现后应复跑本组真实事务测试，不将该构造参数扩充成 DataPort 公共方法。

专用审计登录只获得以下权限：

- public schema USAGE；audit_events INSERT。
- paperpilot_audit schema USAGE 及迁移 006 的 event_fingerprint(uuid) EXECUTE，用于 A 的同事件幂等；不再直接读取审计列。
- users.user_id 和 projects.project_id 列 SELECT，用于 A 的主体/项目存在性检查。

不授予 UPDATE、DELETE、TRUNCATE、ALTER、其他业务内容、审计全文、outbox 或 D 绑定/登记表读取权限。无超级用户/BYPASSRLS/建库/建角色/复制/继承、角色成员关系、创建 schema/临时对象或转授权能力。启动时和每次事务开始/提交前检查权限；指纹函数只允许迁移中的固定 SQL、稳定性、返回类型、search_path、非自身所有者及专属执行授权。函数体、PUBLIC 或转授权能力漂移会拒绝；保留 A 的 event_id 幂等和冲突语义，审计失败时 D 不执行或不返回行。

2026-10-29 升级说明：旧审计登录缺少迁移 006 函数权限，不能仅替换代码继续使用。由管理员先审核 A 迁移与函数所有者，在维护窗口新建本工具登记的审计登录、切换配置并验收，再撤销旧登录；不自动修改未知身份或接管 A 的对象所有者。业务登录改为通过 project_events 读取项目审计，使用同一指纹函数，不再直接读取系统审计行。

防篡改结论针对这个专用登录，不针对可信 DBA 或拥有管理员权限的其他连接。B 的普通业务日志仍沿用业务 DataPort；正式业务身份的权限收敛、DBA 保护与备份由团队部署验收负责。旧手工工厂调用未传 audit_append 时保留 DataPort 审计行为；新的可信启动入口强制使用独立审计绑定。

## 显式安装

先由 A 安装正式迁移并通过现有项目流程取得项目 UUID。管理连接使用独立、空闲的 autocommit 管理员连接；脚本只从指定环境变量读取 DSN 和密码，不接受明文密码参数、不输出秘密。

```powershell
# 事先由本机秘密管理方式注入 PAPERPILOT_SECURITY_ADMIN_DSN。
# 新库尚未安装 D schema 时：
.\.runtime\venv312\Scripts\python.exe scripts/manage_security.py install --admin-dsn-env PAPERPILOT_SECURITY_ADMIN_DSN
# 若已显式安装并核对过 D 的 001 视图，则只增加 002 登记表：
.\.runtime\venv312\Scripts\python.exe scripts/manage_security.py install --existing-views --admin-dsn-env PAPERPILOT_SECURITY_ADMIN_DSN
```

两个 install 用法择一。已有同名 schema/登记表时拒绝覆盖，不自动认领未知对象。显式 install 还撤销本数据库 PUBLIC 的 CREATE/TEMPORARY 和 public schema CREATE；这会影响共享同库用户，须由管理员在选定 PaperPilot 库执行。应用启动不会运行这些 DDL。

新增 migrations/002_service_logins.sql，仅保存本工具创建的角色名、OID、身份类型、项目和撤销时间，不保存密码。读取/审计身份不能访问或修改登记表。

## 开通、轮换、撤销

先将三个身份的不同密码注入各自环境变量。将下方项目 UUID 替换为已存在项目；用户名和环境变量名与运行配置保持一致。

```powershell
.\.runtime\venv312\Scripts\python.exe scripts/manage_security.py audit-create --admin-dsn-env PAPERPILOT_SECURITY_ADMIN_DSN --role pp_audit_gateway --password-env PAPERPILOT_AUDIT_PASSWORD
.\.runtime\venv312\Scripts\python.exe scripts/manage_security.py reader-create --admin-dsn-env PAPERPILOT_SECURITY_ADMIN_DSN --role pp_reader_project_one --project 00000000-0000-0000-0000-000000000001 --password-env PAPERPILOT_PROJECT_ONE_PASSWORD

# 停止该身份的调用方，将环境变量更新为新密码，再执行轮换并重启调用方。
.\.runtime\venv312\Scripts\python.exe scripts/manage_security.py rotate --admin-dsn-env PAPERPILOT_SECURITY_ADMIN_DSN --role pp_reader_project_one --password-env PAPERPILOT_PROJECT_ONE_PASSWORD
.\.runtime\venv312\Scripts\python.exe scripts/manage_security.py revoke --admin-dsn-env PAPERPILOT_SECURITY_ADMIN_DSN --role pp_reader_project_one
```

- CREATE 拒绝所有已存在角色，项目不存在时整个角色/授权/绑定事务回滚。角色名限定 pp_reader_/pp_audit_ 前缀。
- rotate/revoke 同时检查受保护登记表中的角色名和 OID，拒绝未登记身份、OID 不匹配和当前管理登录。不会修改任意已有业务账号。
- 密码先通过 libpq 加密为 SCRAM verifier，再用于角色 DDL，不把明文密码拼进 SQL。参见 [psycopg 密码接口](https://www.psycopg.org/psycopg3/docs/api/pq.html#psycopg.pq.PGconn.encrypt_password)。
- rotate 提交新密码后结束该登记身份的已有会话，旧密码不能重新登录；配置在启动时加载，调用方需要用新秘密重启。需要平滑切换时，可创建新的登记角色、更新配置并重启，再撤销旧角色。
- revoke 提交 NOLOGIN/PASSWORD NULL，撤销本工具授予的权限并移除读取绑定，随后结束该角色已有会话；重复撤销安全，已撤销角色不能通过 rotate 重新启用。保留登记记录，不执行 DROP OWNED、DROP ROLE 或删除业务数据。
- PostgreSQL 角色为集群级；终止范围是该登记角色的会话。若提交后断连或会话清理无法确认，脚本非零退出并要求检查角色/登记状态，不声称改动已回滚，不自动重试。

实际验证位于 test_security_identities_postgres.py，包含最小权限追加/重放、内容读取和篡改拒绝、权限变化时事务回滚、D 独立审计故障关闭、真实密码轮换/旧连接终止/撤销、OID 防误操作、CLI 秘密脱敏和安装拒绝覆盖。均运行在自动清理的独立容器中。
