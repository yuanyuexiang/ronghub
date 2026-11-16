// Compile-only assertions. These functions are never invoked or bundled into UI.
import { typedApi } from "../src/lib/typed-api";
export async function verifyContractTypes() {
  const stations = await typedApi.GET("/api/v1/stations", {
    params: { query: { page: 1 } },
  });
  if (stations.data) {
    const power: string = stations.data.items[0].power_kw;
    // @ts-expect-error Wire decimals are strings, never implicitly JS numbers.
    const incorrectPower: number = stations.data.items[0].power_kw;
    void power;
    void incorrectPower;
  }
  // @ts-expect-error Unknown paths must not silently compile.
  await typedApi.GET("/api/v1/not-a-route");
  await typedApi.GET("/api/v1/stations", {
    // @ts-expect-error Pagination is a number, not an arbitrary string.
    params: { query: { page: "one" } },
  });
  // @ts-expect-error A station path parameter is required.
  await typedApi.GET("/api/v1/stations/{station_id}");
  // @ts-expect-error Required credentials must not be omitted.
  await typedApi.POST("/api/v1/auth/login", { body: { username: "example" } });
}
