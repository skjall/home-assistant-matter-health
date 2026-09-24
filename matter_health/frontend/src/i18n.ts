// Words, numbers and times in the viewer's language.
//
// The page follows the language Home Assistant is set to. Every string lives
// in translations/ui/<language>.json; a key missing there falls back to
// English, and a key missing in English shows itself, so a gap is visible
// instead of silently blank.

type Dict = { [key: string]: string | Dict };

let words: Dict = {};
let english: Dict = {};
export let language = "en";

async function fetchDict(code: string): Promise<Dict> {
  const response = await fetch(`api/i18n/${code}`);
  if (!response.ok) throw new Error(`no translation for ${code}`);
  return (await response.json()) as Dict;
}

/** Load the best available language among the preferred ones. */
export async function loadLanguage(preferred: string[]): Promise<void> {
  const available: string[] = await (await fetch("api/languages")).json();
  const wanted = preferred
    .flatMap((code) => [code.toLowerCase(), code.toLowerCase().split("-")[0]])
    .find((code) => available.includes(code));
  language = wanted ?? "en";
  english = await fetchDict("en");
  words = language === "en" ? english : await fetchDict(language);
  document.documentElement.lang = language;
}

/** Use the given dictionaries directly; for tests and previews. */
export function useWords(code: string, dict: Dict, fallback: Dict = dict): void {
  language = code;
  words = dict;
  english = fallback;
}

// Keys may themselves contain dots (event kinds such as
// "border_router.gone"), so at every level the rest of the key is tried as a
// whole before descending.
function lookup(dict: Dict, key: string): string | undefined {
  const direct = dict[key];
  if (typeof direct === "string") return direct;
  const parts = key.split(".");
  for (let i = 1; i < parts.length; i++) {
    const head = parts.slice(0, i).join(".");
    const branch = dict[head];
    if (branch && typeof branch === "object") {
      const found = lookup(branch, parts.slice(i).join("."));
      if (found !== undefined) return found;
    }
  }
  return undefined;
}

export type Params = Record<string, string | number | null | undefined>;

/** Translate ``key``; ``{name}`` placeholders are filled from ``params``. */
export function t(key: string, params: Params = {}): string {
  let text = lookup(words, key) ?? lookup(english, key) ?? key;
  if (text.includes(" | ")) {
    const [one, other] = text.split(" | ");
    const count = Number(params.count ?? 0);
    text = new Intl.PluralRules(language).select(count) === "one" ? one : other;
  }
  return text.replace(/\{(\w+)\}/g, (whole, name: string) => {
    const value = params[name];
    return value === null || value === undefined ? whole : String(value);
  });
}

/** Whether a translation exists for ``key``. */
export function has(key: string): boolean {
  return lookup(words, key) !== undefined || lookup(english, key) !== undefined;
}

/** "3 minutes", "1 hour" - a length of time in words. */
export function duration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 90) return t("time.seconds", { count: s });
  if (s < 90 * 60) return t("time.minutes", { count: Math.round(s / 60) });
  if (s < 36 * 3600) return t("time.hours", { count: Math.round(s / 3600) });
  return t("time.days", { count: Math.round(s / 86400) });
}

/** "5 minutes ago", "yesterday" - how long ago a moment was. */
export function relative(iso: string, now: Date = new Date()): string {
  const seconds = (new Date(iso).getTime() - now.getTime()) / 1000;
  const format = new Intl.RelativeTimeFormat(language, { numeric: "auto" });
  const abs = Math.abs(seconds);
  if (abs < 60) return format.format(Math.round(seconds), "second");
  if (abs < 3600) return format.format(Math.round(seconds / 60), "minute");
  if (abs < 86400) return format.format(Math.round(seconds / 3600), "hour");
  return format.format(Math.round(seconds / 86400), "day");
}

/** A point in time; today's times without the date. */
export function clock(iso: string, now: Date = new Date()): string {
  const date = new Date(iso);
  const sameDay = date.toDateString() === now.toDateString();
  return new Intl.DateTimeFormat(
    language,
    sameDay
      ? { timeStyle: "short" }
      : { dateStyle: "medium", timeStyle: "short" },
  ).format(date);
}

/** The time of day alone, for lists already grouped by day. */
export function time(iso: string): string {
  return new Intl.DateTimeFormat(language, { timeStyle: "short" }).format(new Date(iso));
}

/** A day as a heading: "today", "yesterday", else "Monday, 3 March". */
export function day(iso: string, now: Date = new Date()): string {
  const date = new Date(iso);
  const midnight = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const days = Math.round((midnight(date).getTime() - midnight(now).getTime()) / 86400000);
  if (days === 0 || days === -1) {
    const word = new Intl.RelativeTimeFormat(language, { numeric: "auto" }).format(days, "day");
    return word.charAt(0).toLocaleUpperCase(language) + word.slice(1);
  }
  return new Intl.DateTimeFormat(language, {
    weekday: "long",
    day: "numeric",
    month: "long",
  }).format(date);
}
