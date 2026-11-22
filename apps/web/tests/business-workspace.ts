import { expect, navigateWorkspace, type Page } from "./fixtures";

/** Follow the explicit return path introduced by prototype correction. */
export async function returnToBusinessList(page: Page, module: string) {
  const close: Record<string, string> = {
    补贴: "关闭补贴详情",
    星级: "关闭评价详情",
    巡检: "关闭任务详情",
    告警: "关闭告警详情",
    督办: "返回督办列表",
  };
  const button = page.getByRole("button", { name: close[module], exact: true });
  if (await button.isVisible()) await button.click();
  await expect(button).not.toBeVisible();
  if (module === "巡检") {
    const list = page.getByRole("heading", {
      name: /^(巡检任务跟踪|企业整改任务)$/,
    });
    if (!(await list.isVisible())) {
      const back = page.getByRole("button", {
        name: "返回任务列表",
        exact: true,
      });
      if (await back.isVisible()) await back.click();
      else await navigateWorkspace(page, /^(巡检任务管理|巡检任务)$/);
    }
    await expect(list).toBeVisible();
    return;
  }
  const formReturn =
    module === "补贴"
      ? "返回补贴申请管理"
      : module === "星级"
        ? "返回评定进度"
        : "";
  if (formReturn) {
    const back = page.getByRole("button", { name: formReturn, exact: true });
    if (await back.isVisible()) {
      await back.click();
      return;
    }
  }
  const views: Record<string, [string, string]> = {
    补贴: ["补贴业务视图", "补贴申请记录"],
    星级: ["星级评价业务视图", "企业申报查看"],
  };
  if (views[module]) {
    const [label, name] = views[module];
    const nav = page.getByRole("navigation", { name: label, exact: true });
    if (!(await nav.isVisible())) {
      await navigateWorkspace(page, name);
      await expect(button).not.toBeVisible();
      return;
    }
    await expect(nav).toBeVisible();
    const item = nav.getByRole("button", { name: name, exact: true });
    if (await item.isVisible()) await item.click();
    else if (module === "星级")
      await nav
        .getByRole("button", { name: "企业申报管理", exact: true })
        .click();
  }
  await expect(button).not.toBeVisible();
}
