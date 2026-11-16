import { typedApi, apiError, type ApiSchemas } from "./typed-api";

function result<T>(response: { data?: T; error?: unknown }): T {
  if (response.data === undefined)
    throw new Error(apiError(response.error, "请检查账号及角色配置"));
  return response.data;
}

export function accountApi(csrf: string) {
  const headers = { "x-csrf-token": csrf };
  return {
    async catalog(manage: boolean) {
      if (manage)
        return result(
          await typedApi.GET("/api/v1/roles", { cache: "no-store" }),
        );
      const data = result(
        await typedApi.GET("/api/v1/accounts/options", { cache: "no-store" }),
      );
      return { ...data, catalog: [], base_roles: {} };
    },
    async list(query: { q: string; page: number }) {
      return result(
        await typedApi.GET("/api/v1/accounts", {
          cache: "no-store",
          params: { query },
        }),
      );
    },
    async operators() {
      return result(
        await typedApi.GET("/api/v1/operators", { cache: "no-store" }),
      );
    },
    async createRole(body: ApiSchemas["app__routers__accounts__ProfileInput"]) {
      return result(await typedApi.POST("/api/v1/roles", { headers, body }));
    },
    async editRole(profile_id: number, body: ApiSchemas["ProfileEdit"]) {
      return result(
        await typedApi.PATCH("/api/v1/roles/{profile_id}", {
          headers,
          params: { path: { profile_id } },
          body,
        }),
      );
    },
    async create(body: ApiSchemas["AccountInput"]) {
      return result(await typedApi.POST("/api/v1/accounts", { headers, body }));
    },
    async edit(user_id: number, body: ApiSchemas["AccountEdit"]) {
      return result(
        await typedApi.PATCH("/api/v1/accounts/{user_id}", {
          headers,
          params: { path: { user_id } },
          body,
        }),
      );
    },
  };
}
