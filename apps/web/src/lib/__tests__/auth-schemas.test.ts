// B1 — Unit tests for the auth form Zod schemas (no DOM needed; node env).

import { describe, it, expect } from "vitest";
import { PASSWORD_MIN_LEN, loginSchema, signupSchema } from "../auth-schemas";

describe("signupSchema", () => {
  it("accepts a valid signup payload", () => {
    const result = signupSchema.safeParse({
      name: "Maya",
      email: "maya@example.com",
      password: "s3cure-pa55word",
    });
    expect(result.success).toBe(true);
  });

  it("rejects an invalid email", () => {
    const result = signupSchema.safeParse({
      email: "not-an-email",
      password: "s3cure-pa55word",
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.error.issues.some((i) => i.path[0] === "email")).toBe(true);
    }
  });

  it(`rejects a password shorter than ${PASSWORD_MIN_LEN}`, () => {
    const result = signupSchema.safeParse({
      email: "maya@example.com",
      password: "short",
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.error.issues.some((i) => i.path[0] === "password")).toBe(
        true,
      );
    }
  });

  it("treats name as optional", () => {
    const result = signupSchema.safeParse({
      email: "maya@example.com",
      password: "s3cure-pa55word",
    });
    expect(result.success).toBe(true);
  });
});

describe("loginSchema", () => {
  it("accepts an email + non-empty password", () => {
    expect(
      loginSchema.safeParse({ email: "a@b.com", password: "x" }).success,
    ).toBe(true);
  });

  it("rejects an empty password", () => {
    expect(
      loginSchema.safeParse({ email: "a@b.com", password: "" }).success,
    ).toBe(false);
  });
});
