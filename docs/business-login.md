# 非管理员业务登录

迁移和安装使用管理员；应用 `PAPERPILOT_DATA_USER/PASSWORD` 使用独立的 `pp_business_*` 登录。D 的 reader、audit 仍分别使用自己的登录和凭据。所有角色都不授予 SUPERUSER、BYPASSRLS、CREATEDB、CREATEROLE、角色成员关系或对象所有权。

显式安装 D 资源会安装 `003_business_login.sql`。已有 002 登记表的数据库，先运行 `scripts/manage_security.py business-upgrade --admin-dsn-env <管理员DSN环境变量名>`，仅扩充受保护登记表的业务类型，不改变已有角色授权。然后运行：

```powershell
python scripts/manage_security.py business-create --admin-dsn-env PP_ADMIN_DSN --role pp_business_app --password-env PP_NEW_BUSINESS_PASSWORD
```

两个命名环境变量应由部署环境注入，命令行不传密码。沿用 `rotate` / `revoke`，登记 role 名称及 OID、防止接管未知账号，轮换/撤销后终止旧会话。不自动安装到未知业务库，不重试未确认的提交。

业务登录只获得 DataPort/索引/outbox 所需表的 SELECT、INSERT，业务状态表 UPDATE 及成员表 DELETE；没有 DDL、TEMP、历史片段/模型/向量/版本/审计 UPDATE 或 DELETE，不访问 D 的 reader 绑定与身份登记表。它仍能读写多个项目的业务数据，项目授权在应用中执行；这是应用服务账号降权，不是底表原生 RLS，也不防御 DBA。

一次性预览默认使用此业务身份。验证：

```powershell
.\.runtime\venv312\Scripts\python.exe scripts/verify_integration.py --business-login backend/tests/data_contracts backend/tests/test_business_login.py --report .runtime/d-business-login-fixed.xml
```

**53 passed**：41 项真实 DataPort 契约加 12 项真实索引、权限拒绝、轮换/撤销验证。数据契约的过期租约和回滚注入仍由隔离测试管理员负责，不给应用额外权限。所有数据库和凭据均为一次性测试资源；正式部署凭据分发由团队环境管理。
