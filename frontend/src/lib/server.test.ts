import { describe, expect, it } from "vitest";
import { normaliseServer } from "./server";

describe("server address on the phone", () => {
  it("adds https and tidies", () => {
    expect(normaliseServer("my-pc.tail1234.ts.net")).toEqual({ url: "https://my-pc.tail1234.ts.net" });
    expect(normaliseServer("  https://my-pc.tail1234.ts.net/  ")).toEqual({ url: "https://my-pc.tail1234.ts.net" });
    expect(normaliseServer("https://ledger.example.com:8443/app/")).toEqual({ url: "https://ledger.example.com:8443/app" });
  });
  it("refuses anything that is not plain https", () => {
    expect(normaliseServer("http://192.168.1.5:8000").error).toMatch(/https/);
    expect(normaliseServer("").error).toBeTruthy();
    expect(normaliseServer("https://user:pw@my-pc.ts.net").error).toMatch(/user name/);
    expect(normaliseServer("https://my-pc.ts.net/?next=evil").error).toBeTruthy();
    expect(normaliseServer("javascript:alert(1)").error).toBeTruthy();
    expect(normaliseServer("https://").error).toBeTruthy();
  });
});
