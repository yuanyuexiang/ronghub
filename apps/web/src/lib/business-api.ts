import { typedApi, apiError, type ApiSchemas } from "./typed-api";

function value<T>(result: { data?: T; error?: unknown }, fallback: string): T {
  if (result.data === undefined)
    throw new Error(apiError(result.error, fallback));
  return result.data;
}

export function subsidyApi(csrf: string, onChanged: () => void) {
  const headers = { "x-csrf-token": csrf };
  async function changed<T>(operation: Promise<{ data?: T; error?: unknown }>) {
    const data = value(await operation, "补贴办理失败，请检查字段或刷新版本");
    onChanged();
    return data;
  }
  return {
    async policies() {
      return value(
        await typedApi.GET("/api/v1/subsidy/policies", { cache: "no-store" }),
        "政策读取失败",
      );
    },
    async energy(station_id: number, signal?: AbortSignal) {
      return value(
        await typedApi.GET("/api/v1/subsidy/energy", {
          signal,
          cache: "no-store",
          params: { query: { station_id } },
        }),
        "电量读取失败",
      );
    },
    async detail(claim_id: number) {
      return value(
        await typedApi.GET("/api/v1/subsidy/claims/{claim_id}", {
          cache: "no-store",
          params: { path: { claim_id } },
        }),
        "补贴详情读取失败",
      );
    },
    create(body: ApiSchemas["ClaimInput"]) {
      return changed(
        typedApi.POST("/api/v1/subsidy/claims", { headers, body }),
      );
    },
    policy(body: ApiSchemas["PolicyInput"]) {
      return changed(
        typedApi.POST("/api/v1/subsidy/policies", { headers, body }),
      );
    },
    batch(body: ApiSchemas["SubsidyBatchInput"]) {
      return changed(
        typedApi.POST("/api/v1/subsidy/claims/batch-actions", {
          headers,
          body,
        }),
      );
    },
    action(
      claim_id: number,
      body: ApiSchemas["app__routers__subsidy__ActionInput"],
    ) {
      return changed(
        typedApi.POST("/api/v1/subsidy/claims/{claim_id}/actions", {
          headers,
          body,
          params: { path: { claim_id } },
        }),
      );
    },
  };
}

export function ratingApi(csrf: string, onChanged: () => void) {
  const headers = { "x-csrf-token": csrf };
  async function changed<T>(operation: Promise<{ data?: T; error?: unknown }>) {
    const data = value(await operation, "评价办理失败，请检查指标或刷新版本");
    onChanged();
    return data;
  }
  return {
    async standards() {
      return value(
        await typedApi.GET("/api/v1/ratings/standards", { cache: "no-store" }),
        "标准读取失败",
      );
    },
    async detail(application_id: number) {
      return value(
        await typedApi.GET("/api/v1/ratings/applications/{application_id}", {
          cache: "no-store",
          params: { path: { application_id } },
        }),
        "评价详情读取失败",
      );
    },
    standard(body: ApiSchemas["StandardInput"]) {
      return changed(
        typedApi.POST("/api/v1/ratings/standards", { headers, body }),
      );
    },
    create(body: ApiSchemas["app__routers__rating__ApplicationInput"]) {
      return changed(
        typedApi.POST("/api/v1/ratings/applications", { headers, body }),
      );
    },
    edit(
      application_id: number,
      body: ApiSchemas["app__routers__rating__EditInput"],
    ) {
      return changed(
        typedApi.PATCH("/api/v1/ratings/applications/{application_id}", {
          headers,
          body,
          params: { path: { application_id } },
        }),
      );
    },
    action(
      application_id: number,
      body: ApiSchemas["app__routers__rating__ActionInput"],
    ) {
      return changed(
        typedApi.POST("/api/v1/ratings/applications/{application_id}/actions", {
          headers,
          body,
          params: { path: { application_id } },
        }),
      );
    },
  };
}
