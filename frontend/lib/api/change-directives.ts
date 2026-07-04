/** Chunk 39 — headers for Change Directive API calls (D296 actor). */

export function changeDirectiveActorHeaders(
  requestingUserId: string,
): Record<string, string> {
  return { "X-Requesting-User": requestingUserId };
}
