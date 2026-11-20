import { navigateWorkspace } from "./fixtures";
import { test, expect } from "./fixtures";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";

test.use({ timezoneId: "UTC" });
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;

test("financial dates use Beijing submission receipts, preserve applied export queries and reset", async ({
  page,
}) => {
  const fixture = JSON.parse(
    execFileSync(
      "uv",
      [
        "run",
        "python",
        "-m",
        "app.browser_fixture",
        "create",
        "--operating",
        "--business-history",
        "30",
        "--business-dates",
      ],
      { cwd: "../server", encoding: "utf8" },
    ),
  );
  try {
    await page.goto("/");
    await page.getByLabel("账号", { exact: true }).fill("operator_a");
    await page.getByLabel("密码", { exact: true }).fill(password);
    await page.getByRole("button", { name: "登录平台", exact: true }).click();
    for (const [label, menu, prefix, ids] of [
      ["补贴", "补贴申报", "SUB", fixture.claim_ids],
      ["星级", "星级申报", "RATE", fixture.rating_ids],
    ] as const) {
      await navigateWorkspace(page, menu);
      await page.getByLabel(`${label}检索名称`).fill(fixture.name);
      await page.getByLabel(`${label}提交开始日期`).fill("2026-11-15");
      await page.getByLabel(`${label}提交结束日期`).fill("2026-11-15");
      await page
        .getByRole("button", { name: `查询${label}记录`, exact: true })
        .click();
      await expect(
        page.getByText(`${label}第1页 · 共25条`, { exact: true }),
      ).toBeVisible();
      const row = page.getByRole("row").filter({
        has: page.getByRole("cell", {
          name: `${prefix}-${ids[25]}`,
          exact: true,
        }),
      });
      await expect(row).toContainText("2026/11/15 00:25:00");
      await page
        .getByRole("button", { name: `${label}下一页`, exact: true })
        .click();
      await expect(
        page.getByText(`${label}第2页 · 共25条`, { exact: true }),
      ).toBeVisible();
      // Editing the draft to an invalid range does not change the applied export.
      await page.getByLabel(`${label}提交开始日期`).fill("2026-11-16");
      const event = page.waitForEvent("download");
      await page
        .getByRole("button", { name: "导出申请清单", exact: true })
        .click();
      const download = await event;
      const csv = readFileSync((await download.path())!, "utf8");
      expect(csv.trim().split(/\r?\n/)).toHaveLength(26);
      const exported = csv
        .trim()
        .split(/\r?\n/)
        .slice(1)
        .map((line) => Number(line.split(",")[0]));
      expect(exported.sort((a, b) => a - b)).toEqual(
        ids.slice(1, 26).sort((a: number, b: number) => a - b),
      );
      await page.getByLabel(`${label}提交开始日期`).fill("2026-11-15");
      if (label === "补贴") {
        await page.getByLabel("补贴周期开始日期").fill("2026-12-01");
        await page.getByLabel("补贴周期结束日期").fill("2026-12-01");
        await page
          .getByRole("button", { name: "查询补贴记录", exact: true })
          .click();
        await expect(
          page.getByText("当前条件下没有补贴记录", { exact: true }),
        ).toBeVisible();
      } else {
        await page.screenshot({
          path: "test-results/business-dates.png",
          fullPage: true,
        });
      }
      await page
        .getByRole("button", { name: `重置${label}筛选`, exact: true })
        .click();
      await expect(page.getByLabel(`${label}提交开始日期`)).toHaveValue("");
      await expect(page.getByLabel(`${label}提交结束日期`)).toHaveValue("");
      if (label === "补贴")
        await expect(page.getByLabel("补贴周期开始日期")).toHaveValue("");
      await page.getByLabel(`${label}检索名称`).fill(fixture.name);
      await expect(
        page.getByRole("button", { name: `查询${label}记录`, exact: true }),
      ).toBeEnabled();
      await page
        .getByRole("button", { name: `查询${label}记录`, exact: true })
        .click();
      await expect(
        page.getByText(`${label}第1页 · 共30条`, { exact: true }),
      ).toBeVisible();
      await expect(page.getByText("无提交时间记录").first()).toBeVisible();
    }
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
        String(fixture.station_id),
      ],
      { cwd: "../server", stdio: "pipe" },
    );
  }
});
