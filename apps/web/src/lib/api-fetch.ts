export async function apiFetch(input: RequestInfo | URL, init?: RequestInit) {
  const response = await fetch(input, init);
  const path = new URL(
    input instanceof Request ? input.url : input.toString(),
    typeof window === "undefined"
      ? "http://ronghub.invalid"
      : window.location.origin,
  ).pathname;
  if (
    typeof window !== "undefined" &&
    response.status === 401 &&
    !path.startsWith("/api/v1/auth/login") &&
    !path.startsWith("/api/v1/auth/me")
  ) {
    window.dispatchEvent(new Event("ronghub:session-expired"));
  }
  return response;
}
