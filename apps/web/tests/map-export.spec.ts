import { test, expect, type Page } from "./fixtures";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";

const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
const fixture = (module: string, args: string[]) =>
  JSON.parse(
    execFileSync("uv", ["run", "python", "-m", module, ...args], {
      cwd: "../server",
      encoding: "utf8",
    }),
  );
async function login(page: Page, username: string) {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill(username);
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录平台", exact: true }).click();
  await expect(page.getByRole("button", { name: "退出登录" })).toBeVisible();
  await page.getByRole("button", { name: "场站地图", exact: true }).click();
}

test("map download uses applied list scope, handles rejection inline and preserves independent role permissions", async ({
  browser,
}) => {
  const station = fixture("app.browser_fixture", ["create"]);
  const limited = fixture("app.permission_fixture", [
    "create",
    "--module",
    "applications",
    "--kind",
    "submit",
  ]);
  const contexts = await Promise.all([
    browser.newContext(),
    browser.newContext(),
    browser.newContext(),
  ]);
  try {
    const [owner, association, readonly] = await Promise.all(
      contexts.map((c) => c.newPage()),
    );
    await login(owner, "operator_a");
    await owner.getByLabel("地图列表范围").selectOption("unlocated");
    await owner.getByLabel("地图场站名称").fill(station.name);
    await owner.getByRole("button", { name: "筛选地图", exact: true }).click();
    const row = owner.getByRole("button", {
      name: `查看地图场站：${station.name}`,
      exact: true,
    });
    await expect(row).toBeVisible();
    const downloadButton = owner.getByRole("button", {
      name: "导出当前范围 CSV",
      exact: true,
    });
    await expect(downloadButton).toBeEnabled();
    await owner.getByLabel("地图场站名称").fill("unapplied-map-query");
    await owner.getByRole("button", { name: "刷新数据", exact: true }).click();
    await expect(downloadButton).toBeEnabled();
    const request = owner.waitForRequest(
      (r) => new URL(r.url()).pathname === "/api/v1/map/export",
    );
    const event = owner.waitForEvent("download");
    await downloadButton.click();
    const url = new URL((await request).url());
    expect(url.searchParams.get("q")).toBe(station.name);
    expect(url.searchParams.get("location")).toBe("unlocated");
    expect(url.searchParams.has("page")).toBe(false);
    const download = await event;
    expect(download.suggestedFilename()).toBe("ronghub-map-stations.csv");
    const csv = readFileSync((await download.path())!, "utf8");
    expect(csv).toContain(station.name);
    expect(csv).not.toContain("unapplied-map-query");
    expect(csv).toContain("来源首次接入时间（北京时间）");
    expect(csv).toContain("暂无数据");
    await owner.route("**/api/v1/map/export?**", (route) =>
      route.fulfill({
        status: 422,
        contentType: "application/json",
        body: JSON.stringify({
          detail: "场站超过10000条，请缩小筛选范围；未导出部分结果",
        }),
      }),
    );
    await downloadButton.click();
    await expect(
      owner.getByRole("alert").filter({ hasText: "场站超过10000条" }),
    ).toBeVisible();
    await expect(row).toBeVisible();
    await expect(downloadButton).toBeEnabled();
    await owner.unroute("**/api/v1/map/export?**");
    await owner.screenshot({
      path: "test-results/map-export-applied.png",
      fullPage: true,
    });

    await login(association, "association");
    await association.getByLabel("地图列表范围").selectOption("unlocated");
    await association.getByLabel("地图场站名称").fill(station.name);
    await association
      .getByRole("button", { name: "筛选地图", exact: true })
      .click();
    await expect(
      association.getByRole("button", {
        name: `查看地图场站：${station.name}`,
        exact: true,
      }),
    ).toBeVisible();
    const associationEvent = association.waitForEvent("download");
    await association
      .getByRole("button", { name: "导出当前范围 CSV", exact: true })
      .click();
    expect(
      readFileSync((await (await associationEvent).path())!, "utf8"),
    ).toContain(station.name);
    expect(
      (
        await association.request.get(
          "/api/v1/statistics/export?start_date=2026-01-01&end_date=2026-01-02",
        )
      ).status(),
    ).toBe(403);
    await login(readonly, limited.username);
    await expect(
      readonly.getByRole("button", { name: "导出当前范围 CSV", exact: true }),
    ).toHaveCount(0);
    expect((await readonly.request.get("/api/v1/map/export")).status()).toBe(
      403,
    );
  } finally {
    await Promise.all(contexts.map((c) => c.close()));
    try {
      execFileSync(
        "uv",
        [
          "run",
          "python",
          "-m",
          "app.permission_fixture",
          "clean",
          "--user",
          String(limited.id),
        ],
        { cwd: "../server", stdio: "pipe" },
      );
    } finally {
      execFileSync(
        "uv",
        [
          "run",
          "python",
          "-m",
          "app.browser_fixture",
          "clean",
          "--station",
          String(station.station_id),
        ],
        { cwd: "../server", stdio: "pipe" },
      );
    }
  }
});
