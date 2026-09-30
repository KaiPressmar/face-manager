/**
 * Identity colours for persons and face boxes.
 *
 * A fixed, validated categorical palette (8 slots, light/dark steps) replaces
 * the former random HSL hashing: those hues were garish and clashed with the
 * tempered UI. The slots live in CSS so they follow the active theme; this
 * module only decides *which* slot an entity gets.
 *
 * Colour is a pairing aid here, never the sole carrier of identity — every
 * badge also spells out the name.
 */
export const IDENTITY_COLOR_SLOTS = 8;

/** Stable slot (1…8) for a name, so an entity keeps its colour everywhere. */
export function identityColorSlot(name: string | null): number {
  if (!name) return 0;
  let hash = 0;
  for (let index = 0; index < name.length; index += 1) {
    hash = name.charCodeAt(index) + ((hash << 5) - hash);
    hash |= 0;
  }
  return (Math.abs(hash) % IDENTITY_COLOR_SLOTS) + 1;
}

/** Theme-aware CSS colour for a name; neutral grey when there is no name. */
export function identityColor(name: string | null): string {
  const slot = identityColorSlot(name);
  return slot === 0 ? "var(--identity-neutral)" : `var(--series-${slot})`;
}
