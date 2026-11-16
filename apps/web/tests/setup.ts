import { execFileSync } from "node:child_process";
// Dedicated localhost demo database only; production throttling stays enabled.
export default function setup() {
  execFileSync(
    "uv",
    ["run", "python", "-m", "app.browser_fixture", "prepare"],
    { cwd: "../server", stdio: "pipe" },
  );
}
