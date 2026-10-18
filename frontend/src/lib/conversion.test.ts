import { describe, it, expect } from "vitest";
import { convertedLabel, conversionDetail, formatAmount } from "./conversion";

const usd = { total_amount: 100, currency: "USD", converted_amount: 307.8, conversion_rate: 3.078, conversion_rate_date: "2026-10-12", conversion_ref: "ILS" };

describe("formatAmount", () => {
  it("groups thousands and keeps two decimals", () => {
    expect(formatAmount(1234.5)).toBe("1,234.50");
    expect(formatAmount(0)).toBe("0.00");
  });
});

describe("convertedLabel (documents table)", () => {
  it("shows the converted amount under a foreign amount", () => {
    expect(convertedLabel(usd)).toBe("≈ 307.80 ILS");
  });
  it("shows nothing for a document already in the reference currency", () => {
    expect(convertedLabel({ ...usd, currency: " ils ", converted_amount: 100, conversion_rate: 1 })).toBeNull();
  });
  it("shows nothing without a conversion", () => {
    expect(convertedLabel({ ...usd, converted_amount: null, conversion_rate: null, conversion_ref: null })).toBeNull();
  });
});

describe("conversionDetail (document page)", () => {
  it("names the amount, the rate and the rate's date", () => {
    expect(conversionDetail(usd)).toBe("≈ 307.80 ILS at 3.078 (2026-10-12)");
  });
  it("keeps four significant digits of a small per-unit rate", () => {
    expect(conversionDetail({ ...usd, currency: "JPY", conversion_rate: 0.0194466, converted_amount: 1.94 })).toBe("≈ 1.94 ILS at 0.01945 (2026-10-12)");
  });
  it("says a foreign amount has no rate yet", () => {
    expect(conversionDetail({ ...usd, converted_amount: null, conversion_rate: null, conversion_rate_date: null, conversion_ref: null })).toBe("No rate yet");
  });
  it("says nothing for the reference currency or without an amount", () => {
    expect(conversionDetail({ ...usd, currency: "ILS", conversion_rate: 1, converted_amount: 100 })).toBeNull();
    expect(conversionDetail({ ...usd, total_amount: null, converted_amount: null, conversion_rate: null, conversion_ref: null })).toBeNull();
    expect(conversionDetail({ ...usd, currency: null, converted_amount: null, conversion_rate: null, conversion_ref: null })).toBeNull();
  });
});
