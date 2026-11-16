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
async function open(page: Page, id: number) {
  await page.getByRole("button", { name: "星级评价", exact: true }).click();
  await page.getByLabel("星级检索编号").fill(String(id));
  await expect(
    page.getByRole("button", { name: "查询星级记录", exact: true }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "查询星级记录", exact: true }).click();
  await page
    .getByRole("row")
    .filter({
      has: page.getByRole("cell", { name: `RATE-${id}`, exact: true }),
    })
    .getByRole("button", { name: "评星详情", exact: true })
    .click();
}
test("association review and publication can be assigned to different staff", async ({
  browser,
}) => {
  const station = fixture("app.browser_fixture", ["create", "--operating"]),
    reviewer = fixture("app.permission_fixture", [
      "create",
      "--module",
      "ratings",
      "--kind",
      "review",
    ]),
    publisher = fixture("app.permission_fixture", [
      "create",
      "--module",
      "ratings",
      "--kind",
      "publish",
    ]);
  const contexts = await Promise.all([
    browser.newContext(),
    browser.newContext(),
    browser.newContext(),
  ]);
  try {
    const [owner, review, publish] = await Promise.all(
      contexts.map((c) => c.newPage()),
    );
    await login(owner, "operator_a");
    await login(review, reviewer.username);
    await login(publish, publisher.username);
    const identity = await (await owner.request.get("/api/v1/auth/me")).json();
    const headers = { "x-csrf-token": identity.csrf_token };
    const standards = await (
      await owner.request.get("/api/v1/ratings/standards")
    ).json();
    const standard = standards.items.find(
      (s: { code: string; version: number }) =>
        s.code === "DEMO-RATING" && s.version === 1,
    );
    const created = await owner.request.post("/api/v1/ratings/applications", {
      headers,
      data: {
        station_id: station.station_id,
        standard_id: standard.id,
        scores: { service: "90", safety: "90" },
        evidence_note: "独立岗位演示依据",
      },
    });
    expect(created.status()).toBe(201);
    const record = await created.json();
    expect(
      (
        await owner.request.post(
          `/api/v1/ratings/applications/${record.id}/actions`,
          { headers, data: { action: "submit", version: 1 } },
        )
      ).status(),
    ).toBe(200);
    await open(review, record.id);
    await expect(
      review.getByRole("button", { name: "配置新标准版本", exact: true }),
    ).toHaveCount(0);
    await expect(
      review.getByRole("button", { name: "发布星级结果", exact: true }),
    ).toHaveCount(0);
    await review.getByLabel("复核-service", { exact: true }).fill("95");
    await review.getByLabel("复核-safety", { exact: true }).fill("95");
    await review
      .getByLabel("评审意见", { exact: true })
      .fill("已核验证据，交由公示岗位发布");
    const reviewed = review.waitForResponse((r) =>
      r.url().endsWith(`/applications/${record.id}/actions`),
    );
    await review
      .getByRole("button", { name: "保存人工复核", exact: true })
      .click();
    expect((await reviewed).status()).toBe(200);
    await expect(
      review.getByRole("row").filter({
        has: review.getByRole("cell", { name: `RATE-${record.id}`, exact: true }),
      }),
    ).toContainText("已复核待公示");
    await expect(
      review.getByLabel("公示有效期至", { exact: true }),
    ).toHaveCount(0);
    await expect(
      review.getByRole("button", { name: "发布星级结果", exact: true }),
    ).toHaveCount(0);
    await open(publish, record.id);
    await expect(
      publish.getByLabel("复核-service", { exact: true }),
    ).toHaveCount(0);
    await expect(
      publish.getByRole("button", { name: "保存人工复核", exact: true }),
    ).toHaveCount(0);
    await expect(
      publish.getByRole("button", { name: "配置新标准版本", exact: true }),
    ).toHaveCount(0);
    await publish
      .getByLabel("评审意见", { exact: true })
      .fill("复核结果确认，按显式有效期公示");
    await publish
      .getByLabel("公示有效期至", { exact: true })
      .fill(new Date(Date.now() + 30 * 86400000).toISOString().slice(0, 10));
    const published = publish.waitForResponse((r) =>
      r.url().endsWith(`/applications/${record.id}/actions`),
    );
    await publish
      .getByRole("button", { name: "发布星级结果", exact: true })
      .click();
    expect((await published).status()).toBe(200);
    await expect(
      publish
        .getByRole("row")
        .filter({
          has: publish.getByRole("cell", {
            name: `RATE-${record.id}`,
            exact: true,
          }),
        }),
    ).toContainText("已公示");
    await expect(
      publish.getByRole("button", { name: "摘牌并留存记录", exact: true }),
    ).toHaveCount(0);
    await expect(publish.getByLabel("评审意见", { exact: true })).toHaveCount(
      0,
    );
    await publish.screenshot({
      path: "test-results/rating-publish-only.png",
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
    for (const user of [reviewer, publisher])
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
