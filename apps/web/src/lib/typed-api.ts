import createClient from "openapi-fetch";
import type { paths, components } from "@ronghub/api-schema";
import { apiFetch } from "./api-fetch";

// Same-origin cookies and session expiry behavior stay identical to existing panels.
// Mutations must explicitly pass the CSRF token obtained from auth/me.
export const typedApi = createClient<paths>({
  credentials: "same-origin",
  fetch: (request) => apiFetch(request),
});
// Applicant-cookie expiry must not expire an unrelated business login.
export const enrollmentApi = createClient<paths>({
  credentials: "same-origin",
});
// Public projections never carry a business cookie or trigger private-session expiry handling.
export const publicApi = createClient<paths>({ credentials: "omit" });
export type ApiSchemas = components["schemas"];
export function apiError(error: unknown, fallback: string): string {
  if (
    typeof error === "object" &&
    error !== null &&
    "detail" in error &&
    typeof error.detail === "string"
  ) {
    return error.detail.slice(0, 1000);
  }
  return fallback;
}
