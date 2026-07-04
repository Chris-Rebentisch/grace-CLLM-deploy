// Chunk 28 D215 — hex SHA-256 helper for `grace_id_hash` telemetry fields.
// Uses Web Crypto API (available in browsers + node >= 19 + jsdom).

export async function sha256Hex(input: string): Promise<string> {
  const bytes = new TextEncoder().encode(input);
  const buf = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(buf))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}
