import { describe, expect, it } from "vitest";
import { change, dmy, dshort, indianGroup, initials, inr, inrShort, rupeesInput, when } from "./format";

describe("money formatting matches the server's", () => {
  it("groups Indian style", () => {
    expect(indianGroup(999)).toBe("999");
    expect(indianGroup(1000)).toBe("1,000");
    expect(indianGroup(100000)).toBe("1,00,000");
    expect(indianGroup(12345678)).toBe("1,23,45,678");
  });
  it("formats paise", () => {
    expect(inr(10_000_050)).toBe("₹1,00,000.50");
    expect(inr(10_000_050, false)).toBe("₹1,00,000");
    expect(inr(-150_000)).toBe("−₹1,500.00");
    expect(inr(5)).toBe("₹0.05");
    expect(inr(null)).toBe("—");
  });
  it("short axis labels", () => {
    expect(inrShort(2_000_000)).toBe("₹20k");
    expect(inrShort(15_000_000)).toBe("₹1.5L");
    expect(inrShort(0)).toBe("₹0");
  });
  it("amount inputs", () => {
    expect(rupeesInput(150_000)).toBe("1500.00");
    expect(rupeesInput(5)).toBe("0.05");
  });
});

describe("dates", () => {
  it("never shift by timezone", () => {
    expect(dmy("2026-11-05")).toBe("05 Nov 2026");
    expect(dshort("2026-01-31")).toBe("31 Jan 26");
  });
  it("timestamps in the owner's zone", () => {
    expect(when("2026-11-18T20:00:00+00:00", "Asia/Kolkata")).toBe("19 Nov 2026, 01:30");
  });
});

describe("helpers", () => {
  it("initials", () => {
    expect(initials("Ravi Kumar")).toBe("RK");
    expect(initials("Mahesh (demo)")).toBe("M");
  });
  it("change", () => {
    expect(change(110, 100)).toEqual({ pct: 10, up: true });
    expect(change(5, 0)).toBeNull();
  });
});
