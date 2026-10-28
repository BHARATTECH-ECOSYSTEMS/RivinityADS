export const money = (n: number | string | null | undefined) => {
  const numVal = typeof n === "number" ? n : Number(n) || 0;
  return `$${numVal.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
};

export const num = (n: number | string | null | undefined) => {
  const numVal = typeof n === "number" ? n : Number(n) || 0;
  return numVal.toLocaleString("en-US");
};

export const ctr = (clicks: number | string | null | undefined, impressions: number | string | null | undefined) => {
  const c = Number(clicks) || 0;
  const imp = Number(impressions) || 0;
  return imp > 0 ? `${((c / imp) * 100).toFixed(2)}%` : "0.00%";
};

export const dateString = (d: string | null | undefined) => {
  if (!d) return "";
  try {
    return new Date(d).toLocaleString();
  } catch {
    return String(d);
  }
};
