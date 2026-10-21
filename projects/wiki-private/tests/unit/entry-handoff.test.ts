import { describe, expect, it } from "vitest";
import { engineeringEntryHref } from "../../src/state";
describe("exact-revision engineering handoff", () => {
  it("preserves the configured host pathname and transmits only entry identity/revision", () => {
    expect(
      engineeringEntryHref(
        "/app/learning/?ignored=private#/growth?card=old",
        "entry_example-1.2",
        3,
      ),
    ).toBe(
      "/app/learning/#/growth?view=entry-context&entry_id=entry_example-1.2&revision=3",
    );
  });
  it.each([
    "",
    "https://other.invalid/",
    "//other.invalid/",
    "/\\other.invalid/",
    "/invalid path/",
  ])("does not guess or cross the host boundary for %s", (base) =>
    expect(engineeringEntryHref(base, "entry", 1)).toBe(""),
  );
  it.each([0, -1, 1.5, Number.NaN, Number.MAX_SAFE_INTEGER + 1])(
    "rejects a non-exact revision %s",
    (revision) => expect(engineeringEntryHref("/", "entry", revision)).toBe(""),
  );
  it.each([
    "../entry",
    "entry?body=secret",
    "entry&token=bad",
    "x".repeat(129),
  ])("rejects invalid opaque entry identity %s", (id) =>
    expect(engineeringEntryHref("/", id, 1)).toBe(""),
  );
});

describe("trusted launcher learning URL boundary", () => {
  it("allows only the exact approved loopback root and does not carry authentication", () =>
    expect(engineeringEntryHref("http://127.0.0.1:49152/", "entry", 3)).toBe(
      "http://127.0.0.1:49152/#/growth?view=entry-context&entry_id=entry&revision=3",
    ));
  it.each([
    "http://127.0.0.1:80/",
    "http://127.0.0.1:65536/",
    "http://127.0.0.1:049152/",
    "http://localhost:49152/",
    "http://127.0.0.1:49152/path",
    "http://127.0.0.1:49152/?token=bad",
    "http://127.0.0.1:49152/#old",
    "http://user:pass@127.0.0.1:49152/",
    "https://127.0.0.1:49152/",
  ])("rejects noncanonical launcher URL %s", (base) =>
    expect(engineeringEntryHref(base, "entry", 1)).toBe(""),
  );
});

it.each(["\r", "\n", "\r\n"])(
  "rejects trailing line endings in trusted origins and opaque IDs %j",
  (ending) => {
    expect(
      engineeringEntryHref("http://127.0.0.1:49152/" + ending, "entry", 1),
    ).toBe("");
    expect(engineeringEntryHref("/", "entry" + ending, 1)).toBe("");
  },
);
