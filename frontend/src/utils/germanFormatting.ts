const germanNumber = new Intl.NumberFormat("de-DE");

export function countWithLabel(count: number, singular: string, plural: string): string {
  return `${germanNumber.format(count)} ${count === 1 ? singular : plural}`;
}
