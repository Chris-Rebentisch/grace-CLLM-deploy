// UUID4 session id generator. Uses crypto.randomUUID when available and
// falls back to a cryptographically-derived implementation for older JS
// environments. Kept tiny on purpose; no external deps.

export function newSessionId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  const bytes = new Uint8Array(16);
  if (typeof crypto !== "undefined" && typeof crypto.getRandomValues === "function") {
    crypto.getRandomValues(bytes);
  } else {
    for (let i = 0; i < 16; i++) bytes[i] = Math.floor(Math.random() * 256);
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40; // version 4
  bytes[8] = (bytes[8] & 0x3f) | 0x80; // variant 10xx
  const h = (n: number) => n.toString(16).padStart(2, "0");
  return (
    `${h(bytes[0])}${h(bytes[1])}${h(bytes[2])}${h(bytes[3])}-` +
    `${h(bytes[4])}${h(bytes[5])}-${h(bytes[6])}${h(bytes[7])}-` +
    `${h(bytes[8])}${h(bytes[9])}-` +
    `${h(bytes[10])}${h(bytes[11])}${h(bytes[12])}${h(bytes[13])}${h(bytes[14])}${h(bytes[15])}`
  );
}
