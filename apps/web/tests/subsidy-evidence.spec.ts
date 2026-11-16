import { test, expect, type Page } from "./fixtures";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { pickStation } from "./station";
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
test("subsidy statement and real ZIP freeze on submit and remain private in review", async ({
  browser,
}) => {
  const fixture = JSON.parse(
    execFileSync(
      "uv",
      ["run", "python", "-m", "app.browser_fixture", "create", "--operating"],
      { cwd: "../server", encoding: "utf8" },
    ),
  );
  const zip = execFileSync("../server/.venv/bin/python", [
    "-c",
    "import io,zipfile,sys; s=io.BytesIO(); z=zipfile.ZipFile(s,'w'); z.writestr('proof.txt','Demo evidence'); z.close(); sys.stdout.buffer.write(s.getvalue())",
  ]);
  const operator = await browser.newContext(),
    regulator = await browser.newContext();
  try {
    const op = await operator.newPage(),
      reg = await regulator.newPage();
    await login(op, "operator_a");
    await login(reg, "regulator");
    await op.getByRole("button", { name: "补贴申报", exact: true }).click();
    await pickStation(op, "补贴场站", fixture);
    await op.getByLabel("补贴申请说明", { exact: true }).fill("本次申报说明");
    const created = op.waitForResponse(
      (r) =>
        r.url().endsWith("/subsidy/claims") && r.request().method() === "POST",
    );
    await op
      .getByRole("button", { name: "核算并创建草稿", exact: true })
      .click();
    const response = await created;
    expect(response.status()).toBe(201);
    const claim = await response.json();
    const evidence = op.getByRole("region", {
      name: "补贴申请说明与材料",
      exact: true,
    });
    await expect(
      op.getByRole("button", { name: "提交补贴审核", exact: true }),
    ).toBeDisabled();
    await expect(
      op.getByLabel("上传补贴ZIP材料", { exact: true }),
    ).toBeEnabled();
    const uploaded = op.waitForResponse(
      (r) =>
        r.url().endsWith(`/subsidy/claims/${claim.id}/attachments`) &&
        r.request().method() === "POST",
    );
    await op.getByLabel("上传补贴ZIP材料", { exact: true }).setInputFiles({
      name: "补贴申报资料.zip",
      mimeType: "application/zip",
      buffer: zip,
    });
    expect((await uploaded).status()).toBe(201);
    await expect(
      evidence.getByRole("link", { name: "补贴申报资料.zip", exact: true }),
    ).toBeVisible();
    await expect(
      op.getByRole("button", { name: "提交补贴审核", exact: true }),
    ).toBeEnabled();
    await op
      .getByLabel("修改补贴申请说明", { exact: true })
      .fill("修改尚未保存");
    const refreshed = op.waitForResponse(
      (r) =>
        r.url().endsWith(`/subsidy/claims/${claim.id}/attachments`) &&
        r.request().method() === "GET",
    );
    await op.getByRole("button", { name: "刷新数据", exact: true }).click();
    expect((await refreshed).status()).toBe(200);
    await expect(
      op.getByLabel("修改补贴申请说明", { exact: true }),
    ).toHaveValue("修改尚未保存");
    await expect(
      op.getByRole("button", { name: "提交补贴审核", exact: true }),
    ).toBeDisabled();
    await op
      .getByRole("button", { name: "保存补贴申请说明", exact: true })
      .click();
    await expect(
      op.getByRole("button", { name: "提交补贴审核", exact: true }),
    ).toBeEnabled();
    const submitted = op.waitForResponse(
      (r) =>
        r.url().endsWith(`/subsidy/claims/${claim.id}/actions`) &&
        r.request().method() === "POST",
    );
    await op.getByRole("button", { name: "提交补贴审核", exact: true }).click();
    expect((await submitted).status()).toBe(200);
    await expect(evidence).toContainText("已提交资料快照 V4");
    await expect(evidence).toContainText("修改尚未保存");
    await expect(op.getByLabel("上传补贴ZIP材料", { exact: true })).toHaveCount(
      0,
    );
    await op.screenshot({
      path: "test-results/subsidy-evidence-frozen.png",
      fullPage: true,
    });
    await reg.getByRole("button", { name: "补贴管理", exact: true }).click();
    await reg
      .getByLabel("补贴检索编号", { exact: true })
      .fill(String(claim.id));
    await reg
      .getByRole("button", { name: "查询补贴记录", exact: true })
      .click();
    await reg
      .getByRole("row")
      .filter({
        has: reg.getByRole("cell", { name: `SUB-${claim.id}`, exact: true }),
      })
      .getByRole("button", { name: "补贴详情", exact: true })
      .click();
    const review = reg.getByRole("region", {
      name: "补贴申请说明与材料",
      exact: true,
    });
    await expect(review).toContainText("修改尚未保存");
    await expect(
      reg.getByLabel("修改补贴申请说明", { exact: true }),
    ).toHaveCount(0);
    const link = review.getByRole("link", {
      name: "补贴申报资料.zip",
      exact: true,
    });
    await expect(link).toBeVisible();
    const download = await reg.request.get((await link.getAttribute("href"))!);
    expect(download.status()).toBe(200);
    expect(await download.body()).toEqual(zip);
    await reg
      .getByLabel("补贴处理意见", { exact: true })
      .fill("已核验说明与ZIP资料");
    await reg
      .getByRole("button", { name: "通过补贴审核", exact: true })
      .click();
    await expect(
      reg.getByText("已审定 · 版本 5", { exact: true }),
    ).toBeVisible();
    const approved = await (
      await reg.request.get(`/api/v1/subsidy/claims/${claim.id}`)
    ).json();
    expect(approved.status).toBe("approved");
    expect(approved.snapshot.submission.note).toBe("修改尚未保存");
    expect(approved.snapshot.submission.version).toBe(4);
  } finally {
    await Promise.allSettled([operator.close(), regulator.close()]);
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
