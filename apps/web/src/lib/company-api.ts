import { typedApi, apiError, type ApiSchemas } from "./typed-api";

function result<T>(response: { data?: T; error?: unknown }): T {
  if (response.data === undefined)
    throw new Error(apiError(response.error, "请检查企业资料或刷新版本"));
  return response.data;
}

export function companyApi(csrf: string) {
  const headers = { "x-csrf-token": csrf };
  return {
    async list(query: { q: string; credit_code: string; page: number }) {
      return result(
        await typedApi.GET("/api/v1/companies", {
          cache: "no-store",
          params: { query },
        }),
      );
    },
    async detail(company_id: number) {
      return result(
        await typedApi.GET("/api/v1/companies/{company_id}", {
          cache: "no-store",
          params: { path: { company_id } },
        }),
      );
    },
    async edit(company_id: number, body: ApiSchemas["app__routers__companies__ProfileInput"]) {
      return result(
        await typedApi.PATCH("/api/v1/companies/{company_id}", {
          headers,
          params: { path: { company_id } },
          body,
        }),
      );
    },
    async status(company_id: number, body: ApiSchemas["StatusInput"]) {
      return result(
        await typedApi.POST("/api/v1/companies/{company_id}/status", {
          headers,
          params: { path: { company_id } },
          body,
        }),
      );
    },
  };
}
