// Trade units vendors actually count in, with typical kg per unit. Crate, bag, box and bundle sizes
// vary by market and packer, so these are starting values the seller can change on the My stock page.
// Assumptions: Kolar tomato crate ≈ 15 kg, onion gunny bag ≈ 50 kg, mango plastic crate ≈ 20 kg.

export type Unit = "kg" | "crate" | "bag" | "box" | "bundle" | "quintal" | "tonne";

export const UNITS: Unit[] = ["kg", "crate", "bag", "box", "bundle", "quintal", "tonne"];
export const PACK_UNITS = ["crate", "bag", "box", "bundle"] as const;
export type PackUnit = (typeof PACK_UNITS)[number];

export const FIXED_KG: Record<"kg" | "quintal" | "tonne", number> = { kg: 1, quintal: 100, tonne: 1000 };

export type UnitSizes = Record<string, Record<PackUnit, number>>;

export const DEFAULT_SIZES: UnitSizes = {
  tomato: { crate: 15, bag: 25, box: 15, bundle: 5 },
  onion: { crate: 25, bag: 50, box: 25, bundle: 5 },
  mango: { crate: 20, bag: 25, box: 10, bundle: 5 },
  cabbage: { crate: 20, bag: 40, box: 20, bundle: 5 },
  okra: { crate: 15, bag: 25, box: 10, bundle: 2 },
  spinach: { crate: 10, bag: 15, box: 10, bundle: 0.5 },
};

const FALLBACK: Record<PackUnit, number> = { crate: 15, bag: 40, box: 10, bundle: 1 };

export function unitKg(sizes: UnitSizes, commodityId: string, unit: Unit): number {
  if (unit === "kg" || unit === "quintal" || unit === "tonne") return FIXED_KG[unit];
  return sizes[commodityId]?.[unit] ?? DEFAULT_SIZES[commodityId]?.[unit] ?? FALLBACK[unit];
}

export function unitLabel(unit: Unit, n: number): string {
  if (unit === "kg") return "kg";
  if (unit === "box") return n === 1 ? "box" : "boxes";
  return n === 1 ? unit : `${unit}s`;
}
