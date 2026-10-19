import { describe, it, expect } from "vitest";
import { convertedLabel, conversionDetail, formatAmount } from "./conversion";

const usd = { total_amount: 100, currency: "USD", converted_amount: 307.8, conversion_rate: 3.078, conversion_rate_date: "2026-10-12", conversion_ref: "ILS" };
const unconverted = { converted_amount: null, conversion_rate: null, conversion_rate_date: null, conversion_ref: null };

describe("formatAmount", () => {
  it("groups thousands and keeps two decimals", () => {
    expect(formatAmount(1234.5)).toBe("1,234.50");
    expect(formatAmount(0)).toBe("0.00");
  });
});

describe("convertedLabel (documents table)", () => {
  it("gives the converted amount and the reference code separately, so the amount never wraps", () => {
    expect(convertedLabel(usd)).toEqual({ amount: "≈ 307.80", ref: "ILS" });
  });
  it("shows nothing for a document already in the reference currency", () => {
    expect(convertedLabel({ ...usd, currency: " ils ", converted_amount: 100, conversion_rate: 1 })).toBeNull();
  });
  it("treats rate 1 as the reference currency, whatever the model wrote (NIS)", () => {
    expect(convertedLabel({ ...usd, currency: "NIS", converted_amount: 100, conversion_rate: 1 })).toBeNull();
  });
  it("shows nothing without a conversion", () => {
    expect(convertedLabel({ ...usd, ...unconverted })).toBeNull();
  });
});

describe("conversionDetail (document page)", () => {
  it("says which way the rate goes and whose date it is", () => {
    expect(conversionDetail(usd)).toBe("≈ 307.80 ILS (1 USD = 3.078 ILS, rate of 2026-10-12)");
  });
  it("keeps four significant digits of a small per-unit rate", () => {
    expect(conversionDetail({ ...usd, currency: "JPY", conversion_rate: 0.0194466, converted_amount: 1.94 })).toBe(
      "≈ 1.94 ILS (1 JPY = 0.01945 ILS, rate of 2026-10-12)",
    );
  });
  it("says a foreign amount is left out of totals, without promising it will convert", () => {
    expect(conversionDetail({ ...usd, ...unconverted })).toBe("No exchange rate yet: not included in converted totals");
  });
  it("says nothing for the reference currency or without an amount", () => {
    expect(conversionDetail({ ...usd, currency: "ILS", conversion_rate: 1, converted_amount: 100 })).toBeNull();
    expect(conversionDetail({ ...usd, currency: "NIS", conversion_rate: 1, converted_amount: 100 })).toBeNull();
    expect(conversionDetail({ ...usd, total_amount: null, ...unconverted })).toBeNull();
    expect(conversionDetail({ ...usd, currency: null, ...unconverted })).toBeNull();
  });
});
