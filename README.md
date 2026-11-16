# RongHub 榕枢

福州市充电监管平台。本期实现监管、协会、运营商三个Web端及客户数据接口，小程序暂缓。

## 目录

| 路径 | 用途 |
|---|---|
| `apps/server/` | Python/FastAPI后端；`app/`业务、`tests/`测试、`alembic/`迁移；API与Worker共享领域代码 |
| `apps/web/` | Next.js/TypeScript三端Web工程 |
| `packages/api-client/` | OpenAPI生成的TypeScript声明；客户端接入持续完善 |
| `tests/e2e/` | 跨端及系统级验收测试 |
| `deploy/` | 部署配置及环境变量模板 |
| `scripts/` | 开发、构建及维护脚本 |
| `docs/requirements/` | 系统需求 |
| `docs/architecture/` | 技术方案 |
| `docs/design/` | 业务流程图及UI设计；旧稿在`archive/` |
| `docs/project/` | 风险及内部估算，初评不是最新报价 |
| `docs/research/` | 开源调研及`evidence/`核验快照 |
| `docs/api/`、`docs/decisions/` | API契约/覆盖清单及架构决策 |
| `references/customer/` | 客户Excel与完整摹客原型包 |
| `references/standards/` | 协议参考资料；适用性需确认 |
| `outputs/` | 交付产物；78,000元报价在`20261109-estimate/`，客户Word在`customer/` |
| `项目上下文.md` | 项目决定、版本与待办交接 |

## 开发状态

首批FastAPI、Next.js及PostgreSQL工程已建立，依赖锁文件和迁移可用。启动及验证见`docs/project/本地开发.md`，进度见`docs/project/工作地图.md`。当前不是完整产品，客户接口及政策规则尚待确认；第三方底座未直接引入。

## 原型预览

在仓库根目录运行 `python3 -m http.server 8000 --bind 127.0.0.1`，访问 `http://127.0.0.1:8000/references/customer/福州监管/`。

## 开工阅读

先读`项目上下文.md`，再读`docs/requirements/系统需求说明书.md`和`docs/architecture/系统技术方案.md`。客户原型为业务基线，在此基础上提升视觉、体验及架构；实质业务差异需确认。

## 容器演示与恢复

单机容器启动、私有附件一致性备份及空环境恢复见[部署与备份恢复](docs/project/部署与备份恢复.md)。该环境独立于本地开发数据库，默认仅访问 `127.0.0.1:3080`。

## API契约

从实际路由生成OpenAPI与前端类型，使用方式和未完成范围见[API契约说明](docs/api/README.md)。在 `apps/web` 运行 `pnpm api:generate` 更新，`pnpm api:check` 检查漂移。
