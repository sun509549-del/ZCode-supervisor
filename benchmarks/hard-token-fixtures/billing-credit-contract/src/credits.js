export function reconcileCredits(rows) {
  const totals = new Map();

  for (const row of rows) {
    const current = totals.get(row.customer) ?? 0;
    const fragment = Math.floor(Number(row.creditDollars) * 100) / 100;
    totals.set(row.customer, current + fragment);
  }

  return Array.from(totals.entries())
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([customer, total]) => ({
      customer,
      credit: Math.round(total * 100) / 100
    }));
}
