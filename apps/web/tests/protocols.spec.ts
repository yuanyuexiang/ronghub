import { navigateWorkspace, test, expect, type Page } from "./fixtures";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
async function login(page: Page, username: string) {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill(username);
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录平台", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "退出登录", exact: true }),
  ).toBeVisible();
}
async function query(page: Page, title: string) {
  await navigateWorkspace(page, "监管协议");
  await page.getByLabel("监管协议检索", { exact: true }).fill(title);
  await page.getByRole("button", { name: "查询协议", exact: true }).click();
}

test("customer protocol publishes from the shared content editor, downloads privately and disappears after withdrawal", async ({
  browser,
}) => {
  const fixture = JSON.parse(
    execFileSync(
      "uv",
      ["run", "python", "-m", "app.browser_fixture", "create"],
      { cwd: "../server", encoding: "utf8" },
    ),
  );
  const contexts = await Promise.all([0, 1, 2].map(() => browser.newContext()));
  try {
    const [reg, owner, association] = await Promise.all(
      contexts.map((c) => c.newPage()),
    );
    for (const [i, page] of [reg, owner, association].entries())
      await login(page, ["regulator", "operator_a", "association"][i]);
    await query(reg, fixture.name);
    await expect(
      reg.getByText("暂无已发布的监管协议", { exact: true }),
    ).toBeVisible();
    await reg
      .getByRole("button", { name: "管理协议内容", exact: true })
      .click();
    await reg
      .getByRole("button", { name: "新建内容草稿", exact: true })
      .click();
    await reg.getByLabel("内容类型", { exact: true }).selectOption("protocol");
    await reg.getByLabel("内容标题", { exact: true }).fill(fixture.name);
    await reg
      .getByLabel("内容正文", { exact: true })
      .fill(
        "客户协议演示正文：<script>window.protocolUnsafe=true</script>。此内容仅供验证，不是正式接入协议。",
      );
    const created = reg.waitForResponse(
      (r) =>
        r.url().endsWith("/api/v1/content") && r.request().method() === "POST",
    );
    await reg
      .getByRole("button", { name: "保存内容草稿", exact: true })
      .click();
    const response = await created;
    expect(response.status()).toBe(201);
    const item = await response.json();
    await query(owner, fixture.name);
    await expect(
      owner.getByText("暂无已发布的监管协议", { exact: true }),
    ).toBeVisible();
    await reg
      .getByRole("button", { name: "编辑草稿正文", exact: true })
      .click();
    await reg.getByLabel("上传内容附件", { exact: true }).setInputFiles({
      name: "监管协议原件.pdf",
      mimeType: "application/pdf",
      buffer: Buffer.from(
        "%PDF-1.4\nSynthetic customer protocol, not official",
      ),
    });
    await expect(
      reg
        .locator(".article-assets")
        .filter({ has: reg.getByLabel("上传内容附件", { exact: true }) }),
    ).toContainText("监管协议原件.pdf");
    await reg.getByLabel("内容正文", { exact: true }).click();
    await reg
      .getByLabel("内容正文", { exact: true })
      .press("ControlOrMeta+End");
    await reg.keyboard.insertText(" 附件是演示文件，不代表正式规范。");
    await reg
      .getByRole("button", { name: "保存内容草稿", exact: true })
      .click();
    await expect(
      reg.getByRole("button", { name: "发布内容", exact: true }),
    ).toBeEnabled();
    await reg.getByRole("button", { name: "发布内容", exact: true }).click();
    await expect(
      reg.getByRole("button", { name: "撤下内容", exact: true }),
    ).toBeVisible();
    await owner.getByRole("button", { name: "刷新数据", exact: true }).click();
    await expect(
      owner.getByText("协议第1页 · 共1条", { exact: true }),
    ).toBeVisible();
    await owner
      .getByLabel("监管协议检索", { exact: true })
      .fill("尚未应用的协议关键词");
    await owner.getByRole("button", { name: "刷新数据", exact: true }).click();
    await expect(owner.getByLabel("监管协议检索", { exact: true })).toHaveValue(
      "尚未应用的协议关键词",
    );
    await expect(
      owner.getByText("协议第1页 · 共1条", { exact: true }),
    ).toBeVisible();
    await owner.setViewportSize({ width: 390, height: 844 });
    let downloadPath = "";
    for (const page of [owner, association]) {
      if (page === association) await query(page, fixture.name);
      await expect(
        page.getByRole("button", { name: "管理协议内容", exact: true }),
      ).toHaveCount(0);
      await page.getByRole("button", { name: "查看协议", exact: true }).click();
      const detail = page.getByRole("region", {
        name: "监管协议正文",
        exact: true,
      });
      await expect(detail).toContainText("此内容仅供验证，不是正式接入协议。");
      await expect(detail.locator("script")).toHaveCount(0);
      expect(
        await page.evaluate(() => Object.hasOwn(window, "protocolUnsafe")),
      ).toBe(false);
      const link = detail.getByRole("link", { name: /监管协议原件.pdf/ });
      downloadPath = (await link.getAttribute("href"))!;
      const downloading = page.waitForEvent("download");
      await link.click();
      const downloaded = await downloading;
      expect(downloaded.suggestedFilename()).toBe("监管协议原件.pdf");
      expect(readFileSync((await downloaded.path())!, "utf8")).toBe(
        "%PDF-1.4\nSynthetic customer protocol, not official",
      );
    }
    expect(
      await owner.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await owner.screenshot({
      path: "test-results/protocol-reader-390.png",
      fullPage: true,
    });
    await association.screenshot({
      path: "test-results/protocol-reader-desktop.png",
      fullPage: true,
    });
    await reg.getByRole("button", { name: "撤下内容", exact: true }).click();
    await expect(reg.locator(".application-detail")).toContainText("已撤下");
    await owner.getByRole("button", { name: "刷新数据", exact: true }).click();
    await expect(
      owner
        .getByRole("region", { name: "监管协议阅读区", exact: true })
        .getByRole("alert"),
    ).toContainText("协议未发布或不可访问");
    await expect(
      owner.getByRole("region", { name: "监管协议正文", exact: true }),
    ).toHaveCount(0);
    expect((await owner.request.get(downloadPath)).status()).toBe(404);
    expect(
      (await owner.request.get(`/api/v1/protocols/${item.id}`)).status(),
    ).toBe(404);
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
        String(fixture.station_id),
      ],
      { cwd: "../server", stdio: "pipe" },
    );
  }
});
