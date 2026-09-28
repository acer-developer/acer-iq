// Only http(s) links are ever rendered as hrefs. Archive links come from
// third-party feeds; a `javascript:` URL behind a "Source" link would run in
// the signed-in BD's session (the backend also strips them - belt and braces).
export const safeUrl = (u) => (/^https?:\/\//i.test(String(u ?? "").trim()) ? String(u).trim() : "");
