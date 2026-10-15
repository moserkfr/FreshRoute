// Reads stock lists as vendors type them in WhatsApp: one item per line, mixed languages,
// trade units, optional grade / age / price. Rule-based and offline; anything it can't read
// is returned as a problem line rather than guessed.
import type { Grade } from "../api/types";
import type { Unit } from "./units";

export interface ParsedLine {
  line: string;
  commodity_id: string;
  qty: number;
  unit: Unit;
  grade: Grade;
  age_days: number;
  cost_per_kg: number | null;
  unit_assumed: boolean;
}

export interface ParseProblem { line: string; why: string }

// English, Hindi/Hinglish, Kannada and Tamil/Telugu names seen in mandi messages.
const COMMODITY_ALIASES: Record<string, string[]> = {
  tomato: ["tomato", "tomatoes", "tomatos", "tamatar", "tamater", "tamaatar", "thakkali", "takkali", "tamata", "ಟೊಮೆಟೊ", "ಟೊಮೇಟೊ", "टमाटर"],
  onion: ["onion", "onions", "pyaz", "pyaaz", "piyaz", "piyaj", "kanda", "eerulli", "irulli", "erulli", "ullagadde", "vengayam", "ullipaya", "ಈರುಳ್ಳಿ", "प्याज"],
  mango: ["mango", "mangoes", "mangos", "aam", "maavu", "mavu", "mavina", "maavina", "mamidi", "ಮಾವು", "ಮಾವಿನ", "आम"],
  okra: ["okra", "bhindi", "bhendi", "bindi", "ladyfinger", "ladies finger", "lady finger", "ladys finger", "bende", "bendekai", "vendakkai", "bendakaya", "ಬೆಂಡೆ", "भिंडी"],
  cabbage: ["cabbage", "cabbages", "patta gobhi", "patta gobi", "band gobhi", "bandh gobi", "patta-gobhi", "kosu", "ele kosu", "elekosu", "muttaikose", "ಎಲೆಕೋಸು", "ಕೋಸು", "पत्तागोभी", "पत्ता गोभी"],
  spinach: ["spinach", "palak", "palak soppu", "palya soppu", "keerai", "palakura", "ಪಾಲಕ್", "पालक"],
};

const UNIT_ALIASES: [Unit, string[]][] = [
  ["quintal", ["quintal", "quintals", "qtl", "qtls", "qt", "kwintal"]],
  ["tonne", ["tonne", "tonnes", "ton", "tons", "tn", "t"]],
  ["kg", ["kg", "kgs", "kilo", "kilos", "kilogram", "kilograms", "ಕೆಜಿ", "किलो"]],
  ["crate", ["crate", "crates", "cr", "crt", "tray", "trays", "petti", "pettige", "ಟ್ರೇ"]],
  ["box", ["box", "boxes", "bx", "carton", "cartons", "peti", "peti's", "पेटी", "ಬಾಕ್ಸ್"]],
  ["bag", ["bag", "bags", "bori", "boriya", "boriyan", "sack", "sacks", "chila", "chilla", "gunny", "ಚೀಲ", "बोरी"]],
  ["bundle", ["bundle", "bundles", "kattu", "kattugalu", "gaddi", "gaddiyan", "jodi", "ಕಟ್ಟು", "गड्डी"]],
];

const TODAY = ["today", "aaj", "ivattu", "ivatthu", "indu", "fresh", "ಇವತ್ತು", "आज"];
const YESTERDAY = ["yesterday", "yday", "yest", "kal", "ninne", "ನಿನ್ನೆ", "कल"];

/** Strip WhatsApp chat prefixes: "[15/10/26, 9:14 am] Ravi: …" or "15/10/26, 9:14 am - Ravi: …". */
function stripChat(line: string): string {
  return line
    .replace(/^\s*\[[^\]]{4,40}\]\s*[^:]{1,40}:\s*/, "")
    .replace(/^\s*\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4},?\s+\d{1,2}:\d{2}(?:\s?[ap]\.?m\.?)?\s+-\s+[^:]{1,40}:\s*/i, "")
    .replace(/^\s*(?:\d{1,2}[.)]|[-*•▪●➤>])\s+/, "") // list markers
    .trim();
}

function findCommodity(text: string): { id: string; at: number; len: number } | null {
  let best: { id: string; at: number; len: number } | null = null;
  for (const [id, names] of Object.entries(COMMODITY_ALIASES)) {
    for (const n of names) {
      const latin = /^[a-z' -]+$/.test(n);
      const re = latin ? new RegExp(`(^|[^a-z])${n.replace(/[-' ]/g, "[-' ]?")}(?=$|[^a-z])`) : null;
      const m = re ? re.exec(text) : null;
      const at = re ? (m ? m.index + m[1].length : -1) : text.indexOf(n);
      if (at >= 0 && (!best || n.length > best.len)) best = { id, at, len: m ? m[0].length - m[1].length : n.length };
    }
  }
  return best;
}

function unitFrom(word: string): Unit | null {
  const w = word.toLowerCase().replace(/[.,:;)]+$/, "");
  for (const [u, names] of UNIT_ALIASES) if (names.includes(w)) return u;
  return null;
}

export function parseStockText(text: string, knownCommodities: string[]): { items: ParsedLine[]; problems: ParseProblem[] } {
  const items: ParsedLine[] = [];
  const problems: ParseProblem[] = [];
  const known = new Set(knownCommodities);
  for (const raw of text.split(/\r?\n/)) {
    const line = stripChat(raw);
    if (!line || /^<media omitted>$|^this message was deleted$/i.test(line)) continue;
    const lower = line.toLowerCase();
    const c = findCommodity(lower);
    if (!c || !known.has(c.id)) {
      if (/\d/.test(lower)) problems.push({ line: raw.trim(), why: "no known commodity name" });
      continue;
    }
    const rest = (lower.slice(0, c.at) + " " + lower.slice(c.at + c.len)).replace(/(\d),(\d{3})/g, "$1$2");

    // price first so "@ 12" / "rs 12/kg" isn't read as the quantity
    let cost: number | null = null;
    const price = /(?:@|₹|rs\.?|inr|rate)\s*(\d+(?:\.\d+)?)\s*(?:\/\s*-?\s*)?(?:per\s*)?(?:\/?\s*kg)?/.exec(rest);
    let body = rest;
    if (price) {
      cost = Number(price[1]);
      body = rest.slice(0, price.index) + " " + rest.slice(price.index + price[0].length);
    }

    // age: today / yesterday / "2 days old" / "3 din"
    let age = 0;
    const ageM = /(\d+)\s*(?:days?|din|dina|dinagalu)\s*(?:old|ago|purana|hale)?/.exec(body);
    if (ageM) {
      age = Number(ageM[1]);
      body = body.slice(0, ageM.index) + " " + body.slice(ageM.index + ageM[0].length);
    } else if (YESTERDAY.some((w) => new RegExp(`(^|\\s)${w}(\\s|$|[.,])`).test(body) || (!/^[a-z]+$/.test(w) && body.includes(w)))) {
      age = 1;
    } else if (TODAY.some((w) => body.includes(w))) {
      age = 0;
    }

    // grade: "A grade", "grade b", "(C)", "A-grade", "2nd quality"
    let grade: Grade = "A";
    const g = /(?:grade|gr\.?|quality)\s*[-:]?\s*([abc])\b|\b([abc])\s*[-]?\s*(?:grade|gr\b|quality|class)|\(([abc])\)|(?:^|\s)([abc])(?:\s|$)/.exec(body);
    if (g) {
      grade = (g[1] ?? g[2] ?? g[3] ?? g[4]).toUpperCase() as Grade;
      body = body.slice(0, g.index) + " " + body.slice(g.index + g[0].length);
    } else if (/\b(2nd|second|medium)\b/.test(body)) grade = "B";
    else if (/\b(3rd|third|low|small)\b/.test(body)) grade = "C";

    // quantity + unit: "120 crates", "300kg", "2.5 t", "crates 40"
    const q = /(\d+(?:\.\d+)?)\s*([^\s\d]*)(?:\s+([^\s\d]+))?/.exec(body);
    if (!q) {
      problems.push({ line: raw.trim(), why: "no quantity" });
      continue;
    }
    const qty = Number(q[1]);
    let unit = unitFrom(q[2] ?? "") ?? unitFrom(q[3] ?? "");
    if (!unit) {
      const before = body.slice(0, q.index).trim().split(/\s+/).pop() ?? "";
      unit = unitFrom(before);
    }
    if (!(qty > 0)) {
      problems.push({ line: raw.trim(), why: "quantity must be more than 0" });
      continue;
    }
    items.push({ line: raw.trim(), commodity_id: c.id, qty, unit: unit ?? "kg", unit_assumed: !unit, grade, age_days: Math.min(age, 120), cost_per_kg: cost });
  }
  return { items, problems };
}
