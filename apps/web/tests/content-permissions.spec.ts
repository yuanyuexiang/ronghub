import { navigateWorkspace, test, expect, Page } from "./fixtures";
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
async function open(page: Page, title: string) {
  await navigateWorkspace(page, /^(公告与通知|新增发布资讯)$/);
  await page.getByLabel("搜索内容标题", { exact: true }).fill(title);
  await page.getByRole("button", { name: "查询内容", exact: true }).click();
  const row = page
    .getByRole("row")
    .filter({ has: page.getByRole("cell", { name: title, exact: true }) });
  await row.getByRole("button", { name: "查看内容", exact: true }).click();
  return row;
}
test("content editor, publisher and withdrawal staff have separate controls", async ({
  browser,
}) => {
  const station = fixture("app.browser_fixture", ["create"]);
  const staff = ["edit", "publish", "withdraw"].map((kind) =>
    fixture("app.permission_fixture", [
      "create",
      "--module",
      "content",
      "--kind",
      kind,
    ]),
  );
  const contexts = await Promise.all(
    [0, 1, 2, 3].map(() => browser.newContext()),
  );
  try {
    const [root, editor, publisher, withdrawer] = await Promise.all(
      contexts.map((c) => c.newPage()),
    );
    for (const [i, page] of [root, editor, publisher, withdrawer].entries())
      await login(page, i === 0 ? "regulator" : staff[i - 1].username);
    const me = await (await root.request.get("/api/v1/auth/me")).json();
    const created = await root.request.post("/api/v1/content", {
      headers: { "x-csrf-token": me.csrf_token },
      data: {
        kind: "announcement",
        title: station.name,
        body: "待岗位编辑的内容",
        audience: "all",
      },
    });
    expect(created.status()).toBe(201);
    await open(editor, station.name);
    await expect(
      editor.getByRole("button", { name: "新建内容草稿", exact: true }),
    ).toHaveCount(0);
    await expect(
      editor.getByRole("button", { name: "发布内容", exact: true }),
    ).toHaveCount(0);
    await expect(
      editor.getByLabel("内容发布时间", { exact: true }),
    ).toHaveCount(0);
    await editor
      .getByRole("button", { name: "编辑草稿正文", exact: true })
      .click();
    await editor
      .getByLabel("内容正文", { exact: true })
      .fill("编辑岗位已核验正文，交发布岗位处理");
    await editor
      .getByRole("button", { name: "保存内容草稿", exact: true })
      .click();
    await expect(
      editor.getByText("编辑岗位已核验正文，交发布岗位处理", { exact: true }),
    ).toBeVisible();
    const publishedRow = await open(publisher, station.name);
    await expect(
      publisher.getByRole("button", { name: "新建内容草稿", exact: true }),
    ).toHaveCount(0);
    await expect(
      publisher.getByRole("button", { name: "编辑草稿正文", exact: true }),
    ).toHaveCount(0);
    await expect(
      publisher.getByLabel("内容发布时间", { exact: true }),
    ).toBeVisible();
    await publisher
      .getByRole("button", { name: "发布内容", exact: true })
      .click();
    await expect(publisher.locator(".record-detail")).toContainText("已发布");
    await expect(
      publisher.getByRole("button", { name: "撤下内容", exact: true }),
    ).toHaveCount(0);
    await publisher.screenshot({
      path: "test-results/content-publish-only.png",
      fullPage: true,
    });
    const withdrawnRow = await open(withdrawer, station.name);
    await expect(
      withdrawer.getByRole("button", { name: "新建内容草稿", exact: true }),
    ).toHaveCount(0);
    await expect(
      withdrawer.getByRole("button", { name: "编辑草稿正文", exact: true }),
    ).toHaveCount(0);
    await withdrawer
      .getByRole("button", { name: "撤下内容", exact: true })
      .click();
    await expect(withdrawer.locator(".record-detail")).toContainText("已撤下");
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
