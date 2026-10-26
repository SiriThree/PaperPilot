# Phase 2B：数据库选型中立的 B 侧接入

数据库引擎、正式模型、迁移、驱动、事务与并发实现由 A/团队决定。B 不指定正式方案，也不在本次新增模型、迁移或数据库依赖。已有 SQLite 模块完整保留为历史实验，已解除与 B 默认工厂的绑定；它不是正式选型结果。

## 替换入口

- `PAPERPILOT_A_MODE=mock`：内存 Mock，不访问任何数据库。
- `persistent` 或兼容名 `real`：必须由可信服务端入口显式提供 `DataBinding`；没有注册即拒绝启动，绝不回退 Mock。仅设置环境变量不足以接入持久化。
- `DataBinding(data, ready, close=None)` 位于 `app.adapters.data_binding`，与冻结的 DataPort 分离。`data` 实现全部 26 方法；`ready()` 成功返回 None/True，失败抛异常或返回 False；可选 `close()` 在服务退出时释放资源。
- 工厂执行初始 readiness；HTTP 健康检查调用同一个独立回调，不读取 `.database`、连接字符串、表名或 ORM 对象。回调故障输出安全错误。不会通过 readiness 自动迁移、初始化用户或回退。
- 明确的 Mock 实例不能注册为持久化；Mock 模式也拒绝额外 binding，避免忽略配置。此结构检查不能证明任意第三方实现真的持久化，仍须通过 A 的隔离库验收。

A 的组合入口示意（省略的初始化由 A 实现，以下不是可直接执行的数据库实现）：

```python
from app.adapters.data_binding import DataBinding
from app.main import create_app as create_business_app

def create_app():
    # A 在这里显式加载自身配置，初始化已选定的适配器及生命周期；失败须安全退出。
    binding = DataBinding(data=adapter, ready=check_ready, close=close_resources)
    return create_business_app(data_binding=binding)
```

启动 A 的组合入口，而非让 B 动态导入客户端指定模块。初始化/迁移失败、连接异常和提交异常必须由 A 分类、脱敏，不得在构造入口打印真实 URL、凭据或原 SQL。构造失败时的资源清理由该入口负责；正常退出时 B 调用注册的 close。

默认业务入口仍按 README 以 Mock 启动。Phase 3 已增加 C/D 独立工厂注册，成员交付真实实现后可分别替换；见 [开发指南](development-guide.md)。生产配置仍明确拒绝。Settings 中保留的 database_path/lease_seconds 仅兼容历史测试构造，默认数据库路径为空，不再读取旧数据库/租约环境变量，也不作为 A 的配置约束。

## AB-1 与业务边界

`ports.py` 的 26 个方法、参数、默认值与公开语义不变。业务服务只调用 DataPort；生命周期不加入该端口。完整输入、返回、缺失、冲突及权限规则见 [AB-1](ab-data-contract.md)。

- 项目权限：B 从会话构建可信 Context，A 在读取/写入/提交时重新校验范围和权限；前端编号不是授权。
- 任务：pending → processing → succeeded/failed，领取增加 attempts；安全重试复用 task_id；迟到 worker 不能覆盖新尝试。外部 AI 在事务外执行。
- 完成结果明确提交后，后续审计失败不能改写任务成功状态；complete_job 返回 DATA_COMMIT_UNKNOWN 或未分类异常时，B 不再调用 fail_job，交由 A 的对账/租约恢复处理。
- 导入、报告快照、版本号、任务结果的原子性由 A 的实现保证；B 不用 Mock 测试替代数据库事务证明。
- 报告历史和引用快照通过既有端口重新读取；历史不能被覆盖，移出论文后历史报告仍受项目权限保护。

## 上传、文件与提交结果

文件存储与数据库提交不是一个事务，B 按随机 storage_key 保存原文件，A 原子采纳 FileReference 并创建关联/任务/幂等记录。

1. 文件保存失败：不调用 import_paper，返回 FILE_STORAGE_UNAVAILABLE；仅清理本次已创建的部分文件。若 xb 创建因路径存在而失败，不碰原文件。
2. 明确没有提交：仅 AB-1 的 RESOURCE_NOT_FOUND、OWNER_REQUIRED、DATA_CONTRACT_ERROR、DUPLICATE_PAPER、IDENTIFIER_CONFLICT、IDEMPOTENCY_CONFLICT、DATA_UNAVAILABLE 允许清理本次新文件。A 必须仅在确认回滚时返回 DATA_UNAVAILABLE。
3. 提交未知或未分类错误：保留文件，统一返回脱敏 DATA_COMMIT_UNKNOWN；不能因超时/未知 provider 错误认定已回滚。
4. 相同幂等键重放成功：返回原 paper_id/task_id，只清理重放请求产生的多余文件，保留已采纳文件。
5. 浏览器在当前页面保留上传键；报告保存按相同正文/引用/基础版本载荷保留键。无确认结果时同请求重试用原键，成功或改变载荷才开始新操作。当前没有跨刷新恢复凭据；刷新、关闭页面、改变不明请求的内容前需先对账，不能据此声称浏览器有可靠持久化队列。

A 仍需决定：幂等记录保留期、未知提交查询/重放时的状态、文件与数据库的对账方式、孤立文件保留期和人工处理规则。AB-1 没有列举全部存储引用的管理接口，B 不能扫描文件后仅凭“暂时查不到”自动删除；本次没有创建清理任务或擅自扩展 DataPort。

## 可复用共享测试

```powershell
# 默认与数据库无关的行为；恢复/真实并发门槛跳过，不能计作通过
.\.venv\Scripts\python.exe -m pytest backend/tests/data_contracts -q -rs
.\.venv\Scripts\python.exe -m pytest -q -rs

# A 提供显式隔离库 provider 后执行同一套行为与恢复测试
.\.venv\Scripts\python.exe -m pytest backend/tests/data_contracts --data-contract-provider your_package:build_harness -q -rs

# 历史实验仍可显式验证，不是团队选型或正式库验收
.\.venv\Scripts\python.exe -m pytest backend/tests/data_contracts --data-contract-provider data_contracts.sqlite_provider:build_harness -q -rs
.\.venv\Scripts\python.exe -m pytest backend/tests/test_persistence.py backend/tests/test_persistent_restart.py --legacy-persistence -q -rs
```

provider 用 `build_harness(*,tmp_path)` context manager 返回 DataHarness，负责独立临时库、三个测试身份、写后提交前故障探针、reopen 和 expire_claim。不得读取用户运行库配置；不得把内存双对象重连称为持久化。缺少真实恢复能力时应失败或明确跳过，不伪造成功。

现有共享覆盖重复导入、幂等冲突、任务重试及迟到写入、报告版本/引用、权限撤销与跨项目替换；新增重连后成员、证据、审计及导入/保存幂等记录恢复。并发导入、报告 CAS、领取、租约恢复仅可按各数据库实际运行结果报告。

新增 B 注册测试使用明确的内存委托替身，仅证明注入/readiness/关闭/安全失败路径，不是持久化测试。浏览器未知提交测试在真实 Mock 业务请求成功后模拟丢失确认，验证上传与报告重试沿用原键；不是数据库断连实验。

## 等待 A/团队决定

正式数据库及驱动、模型/约束/索引、迁移工具和执行时机、连接池/事务/并发策略、lease/续租/死信、正式用户与会话、文件/正文/向量边界、领域审计/outbox、未知提交对账与备份恢复。A/D 共同决定数据库角色、受控视图和适用的行级隔离，B 不将历史 SQLite 防护视作正式安全方案。

Phase 2C 已完成 [任务恢复交互](task-recovery.md)：刷新或轮询超时后按已知 task_id 重新读取；未知提交显式同键核对。上传/问答/证据/草稿的轻量记录可在同一标签页刷新后恢复，不包含原始正文；报告版本保存的 pendingSave 键仍仅在页面内存，不应据此声称跨刷新版本保存对账已完成。下一步与 A 对齐幂等保留和正式恢复验收，再用 A 的 binding 跑既有业务和浏览器测试。
