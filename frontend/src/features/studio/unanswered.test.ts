import { describe, expect, it } from "vitest";
import { sectionFor } from "./UnansweredQuestions";

describe("unanswered questions", () => {
  it("turns a customer's question into an article section heading", () => {
    expect(sectionFor("do you ship   internationally?")).toBe("## Do you ship internationally\n\n");
    expect(sectionFor("Where can I find warranty info?!")).toBe("## Where can I find warranty info\n\n");
  });
});
