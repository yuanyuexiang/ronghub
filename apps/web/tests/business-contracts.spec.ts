import { navigateWorkspace } from "./fixtures";
import { test, expect } from "./fixtures";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";

const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
test("typed business detail handles missing historical calculation and rule facts", async ({
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
        "1",
      ],
      { cwd: "../server", encoding: "utf8" },
    ),
  );
  try {
    execFileSync(
      "uv",
      [
        "run",
        "python",
        "-c",
        `
import sys
from sqlalchemy import select
from app.config import settings
from app.db import SessionLocal, engine
from app.models import Station, SubsidyClaim, RatingApplication
assert settings.demo_mode and engine.url.host in {'localhost','127.0.0.1'}
with SessionLocal.begin() as db:
    station=db.get(Station,int(sys.argv[1]))
    assert station and station.source=='browser-test' and station.name==sys.argv[2]
    claims=list(db.scalars(select(SubsidyClaim).where(SubsidyClaim.station_id==station.id)))
    ratings=list(db.scalars(select(RatingApplication).where(RatingApplication.station_id==station.id)))
    assert len(claims)==len(ratings)==1
    claims[0].snapshot={}
    ratings[0].rule_snapshot={}
    ratings[0].before_result={}
    ratings[0].calculation={}
`,
        String(fixture.station_id),
        fixture.name,
      ],
      { cwd: "../server", stdio: "pipe" },
    );
    await page.goto("/");
    await page.getByLabel("账号", { exact: true }).fill("operator_a");
    await page.getByLabel("密码", { exact: true }).fill(password);
    await page.getByRole("button", { name: "登录平台", exact: true }).click();
    for (const [label, menu, detail] of [
      ["补贴", "补贴申报", "补贴详情"],
      ["星级", "星级申报", "评星详情"],
    ] as const) {
      await navigateWorkspace(page, menu);
      await page.getByLabel(`${label}检索名称`).fill(fixture.name);
      await page
        .getByRole("button", { name: `查询${label}记录`, exact: true })
        .click();
      await expect(
        page.getByText(`${label}第1页 · 共1条`, { exact: true }),
      ).toBeVisible();
      await page.getByRole("button", { name: detail, exact: true }).click();
      if (label === "补贴") {
        await expect(
          page.getByText("历史核算明细未留存，未使用当前政策或零金额补写。", {
            exact: true,
          }),
        ).toBeVisible();
      } else {
        const info = page.getByTestId("rating-submission-info");
        await expect(info).toContainText(fixture.name);
        await expect(info).toContainText("无提交账号记录");
        await expect(info).toContainText("无提交时间记录");
        await expect(
          page.getByText("历史指标快照未留存，不使用当前标准补写评分依据。", {
            exact: true,
          }),
        ).toBeVisible();
        await expect(
          page.getByText("评审总分 缺失待核实 · 未定级", { exact: true }),
        ).toBeVisible();
        await expect(
          page.getByText("关联 RATE-null", { exact: false }),
        ).toHaveCount(0);
        await page.screenshot({
          path: "test-results/business-contracts-legacy.png",
          fullPage: true,
        });
      }
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
