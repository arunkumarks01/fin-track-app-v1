/**
 * Required configuration — nothing hardcoded (ARCHITECTURE §2 law 7).
 * A missing or malformed value fails the build/boot instead of silently defaulting.
 */
export function requireEnv(
  name: string,
  env: Record<string, string | undefined> = process.env,
): string {
  const value = env[name]?.trim();
  if (!value) {
    throw new Error(`Missing required environment variable ${name}`);
  }
  return value;
}

export function requireHttpUrl(
  name: string,
  env: Record<string, string | undefined> = process.env,
): string {
  const raw = requireEnv(name, env);
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new Error(`${name} is not a valid URL`);
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error(`${name} must be an http(s) URL`);
  }
  return raw.replace(/\/+$/, "");
}
