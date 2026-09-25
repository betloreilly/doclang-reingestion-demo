import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatSeconds(value?: number | null, digits = 2): string {
  if (value === undefined || value === null || Number.isNaN(value)) return "—";
  return `${value.toFixed(digits)} s`;
}

export function formatUsd(value?: number | null, digits = 2): string {
  if (value === undefined || value === null || Number.isNaN(value)) return "Unavailable";
  return `$${value.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;
}

export function formatPct(value?: number | null, digits = 1): string {
  if (value === undefined || value === null || Number.isNaN(value)) return "Unavailable";
  return `${value.toFixed(digits)}%`;
}

export function formatRatio(value?: number | null, digits = 1): string {
  if (value === undefined || value === null || Number.isNaN(value)) return "Unavailable";
  return `${value.toFixed(digits)}×`;
}

export function formatInt(value?: number | null): string {
  if (value === undefined || value === null) return "—";
  return value.toLocaleString();
}
