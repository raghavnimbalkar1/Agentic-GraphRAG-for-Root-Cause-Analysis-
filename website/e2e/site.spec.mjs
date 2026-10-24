import { expect, test } from "@playwright/test";

for (const [width, height] of [
  [1440, 900],
  [768, 1024],
  [390, 844],
  [360, 667],
  [320, 568],
]) {
  test(`${width}px: page, graph, images and text stay in bounds`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto("/");
    await page.evaluate(() => document.fonts.ready);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(
      "AgenticGraphRAG.",
    );
    await expect(page.locator(".map-node")).toHaveCount(12);
    const measurements = await page.evaluate(() => ({
      overflow: document.documentElement.scrollWidth - innerWidth,
      clippedNodes: [...document.querySelectorAll(".map-node")].filter((n) => {
        const r = n.getBoundingClientRect();
        return r.x < 0 || r.right > innerWidth;
      }).length,
      heroEnd: document.querySelector(".hero").getBoundingClientRect().bottom,
      h: innerHeight,
      ink: (() => {
        const c = document.querySelector("canvas");
        const data = c
          .getContext("2d")
          .getImageData(0, 0, c.width, c.height).data;
        let count = 0;
        for (let i = 3; i < data.length; i += 4) if (data[i]) count++;
        return count;
      })(),
    }));
    expect(measurements.overflow).toBeLessThanOrEqual(1);
    expect(measurements.clippedNodes).toBe(0);
    expect(measurements.heroEnd).toBeLessThan(measurements.h);
    expect(measurements.ink).toBeGreaterThan(200);
    await page
      .getByRole("button", { name: "Inspect paymentservice", exact: true })
      .click();
    await expect(
      page.getByRole("button", { name: "Inspect paymentservice", exact: true }),
    ).toHaveAttribute("aria-pressed", "true");
    await page
      .getByRole("button", {
        name: "Enlarge the operations workspace screenshot",
      })
      .click();
    await expect(page.locator("dialog")).toBeVisible();
    expect(
      await page
        .locator("dialog img")
        .evaluate((img) => img.complete && img.naturalWidth > 0),
    ).toBe(true);
    await page
      .getByRole("button", { name: "Close image", exact: true })
      .click();
    await expect(page.locator("dialog")).not.toBeVisible();
    await page.screenshot({
      path: `test-results/site-${width}.png`,
      fullPage: true,
    });
    expect(errors).toEqual([]);
  });
}

test("walkthrough controls, tabs and destructive fallback are explicit", async ({
  page,
}) => {
  await page.goto("/");
  await page
    .getByRole("button", { name: "Redis fallback", exact: true })
    .click();
  await page.getByRole("tab", { name: "04 Execute" }).click();
  await expect(page.getByRole("tabpanel")).toContainText(
    "This is destructive.",
  );
  await page.getByRole("button", { name: "Next step", exact: true }).click();
  await expect(page.getByRole("tab", { name: "05 Verify" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await page.getByRole("tab", { name: "05 Verify" }).press("ArrowRight");
  await expect(page.getByRole("tabpanel")).toContainText("1,625");
  await page
    .getByRole("button", { name: "Reset walkthrough", exact: true })
    .click();
  await expect(page.getByRole("tab", { name: "01 Detect" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(page.locator(".illustrative-label")).toContainText(
    "NOT A LIVE RUN",
  );
});

test("mobile navigation and setup mode switches work", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page
    .getByRole("button", { name: "Open navigation", exact: true })
    .click();
  await page
    .getByRole("navigation")
    .getByRole("link", { name: "Get started", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Open navigation", exact: true }),
  ).toHaveAttribute("aria-expanded", "false");
  await page.getByRole("button", { name: "03 Observe", exact: true }).click();
  await expect(page.locator(".terminal pre")).toContainText(
    "--observe-only --record-evidence",
  );
  await expect(page.locator(".start-limit")).toContainText(
    "No pip, npm or Homebrew",
  );
});
