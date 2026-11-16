# RongHub API类型

`schema.d.ts` 为已锁定版本openapi-typescript生成的paths/operations/components定义，不手工修改，不是已发布的npm包。源文件是 `docs/api/openapi.json`，来自真实FastAPI路由及响应模型。

在 `apps/web` 执行 `pnpm api:generate` 更新，`pnpm api:check` 拒绝漂移，`pnpm typecheck` 验证调用。前端通过 `@ronghub/api-schema` 类型别名引用，不将声明文件放入浏览器运行包；实际请求封装在 `apps/web/src/lib/typed-api.ts`。

未知响应保持unknown，不能用类型断言伪装为业务模型；完整覆盖状况见 `docs/api/契约覆盖清单.md`。本类型库不是客户上游接口SDK。
