import { test, expect, Page } from "./fixtures";
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
async function login(page: Page, name: string) {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill(name);
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录平台" }).click();
  await expect(page.getByRole("button", { name: "退出登录" })).toBeVisible();
}
async function open(page: Page, id: number, operator: boolean) {
  await page
    .getByRole("button", {
      name: operator ? "整改任务" : "巡检督办",
      exact: true,
    })
    .click();
  await page.getByLabel("巡检检索编号").fill(String(id));
  await page.getByRole("button", { name: "查询巡检记录", exact: true }).click();
  const row = page.getByRole("row").filter({
    has: page.getByRole("cell", { name: `TASK-${id}`, exact: true }),
  });
  await row.getByRole("button", { name: "处理任务", exact: true }).click();
  return row;
}
test("inspection evidence, reply and closure can be assigned independently", async ({
  browser,
}) => {
  const station = fixture("app.browser_fixture", ["create"]);
  const staff = ["materials", "reply", "close"].map((kind) =>
    fixture("app.permission_fixture", [
      "create",
      "--module",
      "inspections",
      "--kind",
      kind,
    ]),
  );
  const contexts = await Promise.all(
    [0, 1, 2, 3].map(() => browser.newContext()),
  );
  try {
    const [root, materials, reply, close] = await Promise.all(
      contexts.map((c) => c.newPage()),
    );
    for (const [i, page] of [root, materials, reply, close].entries())
      await login(page, i === 0 ? "regulator" : staff[i - 1].username);
    const identity = await (await root.request.get("/api/v1/auth/me")).json();
    const created = await root.request.post("/api/v1/inspections", {
      headers: { "x-csrf-token": identity.csrf_token },
      data: {
        station_id: station.station_id,
        title: "岗位独立整改验证",
        description: "独立材料、整改回复与复核岗位",
        severity: "major",
        due_at: new Date(Date.now() + 3 * 86400000).toISOString(),
      },
    });
    expect(created.status()).toBe(201);
    const task = await created.json();
    await open(materials, task.id, true);
    await expect(
      materials.getByLabel("巡检处理说明", { exact: true }),
    ).toHaveCount(0);
    await expect(
      materials.getByRole("button", { name: "提交整改回复", exact: true }),
    ).toHaveCount(0);
    await materials.getByLabel("上传整改材料", { exact: true }).setInputFiles({
      name: "独立材料.pdf",
      mimeType: "application/pdf",
      buffer: Buffer.from("%PDF-1.4\nfixture"),
    });
    await expect(
      materials.getByRole("link", { name: "独立材料.pdf", exact: true }),
    ).toBeVisible();
    const replyRow = await open(reply, task.id, true);
    await expect(reply.getByLabel("上传整改材料", { exact: true })).toHaveCount(
      0,
    );
    await expect(
      reply.getByRole("link", { name: "独立材料.pdf", exact: true }),
    ).toBeVisible();
    await reply
      .getByLabel("巡检处理说明", { exact: true })
      .fill("已核对同企业材料岗位上传的整改证据");
    await reply
      .getByLabel("巡检执行联系人", { exact: true })
      .fill("分岗执行人");
    await reply
      .getByLabel("巡检执行联系方式", { exact: true })
      .fill("13800000001");
    await reply
      .getByLabel("巡检填报执行时间", { exact: true })
      .fill("2026-11-01T10:00");
    await reply
      .getByRole("button", { name: "提交整改回复", exact: true })
      .click();
    await expect(replyRow).toContainText("待复核");
    await expect(reply.getByLabel("巡检处理说明", { exact: true })).toHaveCount(
      0,
    );
    const closeRow = await open(close, task.id, false);
    await expect(
      close.getByRole("button", { name: "下发任务", exact: true }),
    ).toHaveCount(0);
    await expect(
      close.getByRole("button", { name: "退回补充整改", exact: true }),
    ).toHaveCount(0);
    await expect(close.getByLabel("上传整改材料", { exact: true })).toHaveCount(
      0,
    );
    await close
      .getByLabel("巡检处理说明", { exact: true })
      .fill("核验证据，关闭任务");
    await close
      .getByRole("button", { name: "复核关闭任务", exact: true })
      .click();
    await expect(closeRow).toContainText("已关闭");
    await expect(close.getByLabel("巡检处理说明", { exact: true })).toHaveCount(
      0,
    );
    await close.screenshot({
      path: "test-results/inspection-close-only.png",
      fullPage: true,
    });
  } finally {
    await Promise.allSettled(contexts.map((c) => c.close()));
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
    for (const user of staff)
      execFileSync(
        "uv",
        [
          "run",
          "python",
          "-m",
          "app.permission_fixture",
          "clean",
          "--user",
          String(user.id),
        ],
        { cwd: "../server", stdio: "pipe" },
      );
  }
});
