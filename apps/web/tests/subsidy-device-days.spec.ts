import { test, expect, type Page } from "./fixtures";
import { execFileSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
async function login(page: Page, name: string) {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill(name);
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录平台", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "城市充电，一目了然", exact: true }),
  ).toBeVisible();
}

test("worker device days distinguish zero and missing with scoped applied-query exports", async ({
  browser,
}) => {
  const contexts = await Promise.all([
    browser.newContext(),
    browser.newContext(),
    browser.newContext(),
  ]);
  try {
    const [manager, owner, other] = await Promise.all(
      contexts.map((c) => c.newPage()),
    );
    await login(manager, "regulator");
    await login(owner, "operator_a");
    await login(other, "operator_b");
    const reg = await (await manager.request.get("/api/v1/auth/me")).json(),
      identity = await (await owner.request.get("/api/v1/auth/me")).json();
    const created = await manager.request.post("/api/v1/sync/tasks", {
      headers: { "x-csrf-token": reg.csrf_token },
      data: {
        operator_id: identity.operator_id,
        request_key: "device-browser-" + randomBytes(8).toString("hex"),
      },
    });
    expect(created.status()).toBe(202);
    const taskId = (await created.json()).id;
    execFileSync(
      "uv",
      [
        "run",
        "python",
        "-c",
        "import sys;from app.sync import claim,execute;job=claim(int(sys.argv[1]));assert job and execute(job)",
        String(taskId),
      ],
      { cwd: "../server", stdio: "pipe" },
    );
    const receipt = (
      await (await manager.request.get("/api/v1/sync/tasks")).json()
    ).items.find((row: { id: number }) => row.id === taskId);
    expect(receipt.status).toBe("succeeded");
    expect(receipt.result.subsidy_device_days).toBe(3);
    await owner.getByRole("button", { name: "补贴申报", exact: true }).click();
    const section = owner.getByRole("region", {
      name: "设备日观测",
      exact: true,
    });
    await expect(
      section.getByText("选择条件后查询；未上报的设备或日期不按零值补齐。", {
        exact: true,
      }),
    ).toBeVisible();
    let reads = 0;
    owner.on("request", (request) => {
      if (new URL(request.url()).pathname === "/api/v1/subsidy/device-days")
        reads++;
    });
    await owner.getByRole("button", { name: "刷新数据", exact: true }).click();
    await expect(
      owner.getByRole("button", { name: "刷新数据", exact: true }),
    ).toBeEnabled();
    expect(reads).toBe(0);
    for (const [label, value] of [
      ["设备日观测开始日期", "2026-11-01"],
      ["设备日观测结束日期", "2026-11-03"],
      ["设备日观测来源", "demo"],
      ["设备日观测设备编码", "equipment-1"],
    ])
      await section.getByLabel(label, { exact: true }).fill(value);
    const queried = owner.waitForResponse(
      (r) => new URL(r.url()).pathname === "/api/v1/subsidy/device-days",
    );
    await section
      .getByRole("button", { name: "查询设备日观测", exact: true })
      .click();
    const response = await queried;
    expect(response.status()).toBe(200);
    const payload = await response.json();
    expect(payload.total).toBe(3);
    await expect(
      section.getByText("第1页 · 共3条设备日观测", { exact: true }),
    ).toBeVisible();
    const zero = section.getByRole("row").filter({ hasText: "2026-11-02" });
    await expect(zero.getByRole("cell").nth(3)).toContainText("0.000000");
    await expect(zero).toContainText("完整日计量");
    const missing = section.getByRole("row").filter({ hasText: "2026-11-03" });
    await expect(missing.getByRole("cell").nth(3)).toContainText("未知");
    await expect(missing).toContainText("部分覆盖");
    await expect(missing).toContainText("不可结算");
    const denied = await other.request.get("/api/v1/subsidy/device-days", {
      params: {
        start_date: "2026-11-01",
        end_date: "2026-11-03",
        station_id: payload.items[0].station_id,
      },
    });
    expect(denied.status()).toBe(200);
    expect((await denied.json()).total).toBe(0);
    await section
      .getByLabel("设备日观测来源", { exact: true })
      .fill("unapplied");
    const refreshed = owner.waitForResponse(
      (r) => new URL(r.url()).pathname === "/api/v1/subsidy/device-days",
    );
    await owner.getByRole("button", { name: "刷新数据", exact: true }).click();
    expect(new URL((await refreshed).url()).searchParams.get("source")).toBe(
      "demo",
    );
    await expect(
      section.getByLabel("设备日观测来源", { exact: true }),
    ).toHaveValue("unapplied");
    await expect(
      section.getByLabel("设备日观测已应用条件", { exact: true }),
    ).toContainText("来源：demo");
    await expect(
      section.getByLabel("设备日观测已应用条件", { exact: true }),
    ).not.toContainText("unapplied");
    const exported = owner.waitForResponse(
        (r) =>
          new URL(r.url()).pathname === "/api/v1/subsidy/device-days/export",
      ),
      download = owner.waitForEvent("download");
    await section
      .getByRole("button", { name: "导出设备日观测CSV", exact: true })
      .click();
    await download;
    const csv = await exported;
    expect(csv.status()).toBe(200);
    expect(new URL(csv.url()).searchParams.get("source")).toBe("demo");
    expect(await csv.text()).toContain("未核验，不可结算");
    await section.screenshot({
      path: "../../.backups/subsidy-device-days.png",
    });
    await owner.route("**/api/v1/subsidy/device-days?**", (route) =>
      route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ detail: "设备来源暂不可用" }),
      }),
    );
    await owner.getByRole("button", { name: "刷新数据", exact: true }).click();
    await expect(section.getByRole("alert")).toContainText("设备来源暂不可用");
    await expect(
      section.getByText(
        "当前条件下没有已上报设备日记录；不代表零电量或零故障。",
        { exact: true },
      ),
    ).toHaveCount(0);
    await owner.unroute("**/api/v1/subsidy/device-days?**");
    await owner.getByRole("button", { name: "刷新数据", exact: true }).click();
    await expect(
      section.getByText("第1页 · 共3条设备日观测", { exact: true }),
    ).toBeVisible();
  } finally {
    await Promise.allSettled(contexts.map((c) => c.close()));
  }
});
