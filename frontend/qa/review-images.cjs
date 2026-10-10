// Run with NODE_PATH pointing to a disposable Playwright install; all API calls are fixtures.
const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const shapes = [
  [600, 1000],
  [1200, 600],
  [800, 800],
  [1800, 300],
  [300, 1800],
];
const out = process.env.QA_OUTPUT || "/tmp/dropgrid-image-qa";
fs.mkdirSync(out, { recursive: true });
const base = process.env.QA_ORIGIN || "http://localhost:5173";
const summary = {
  id: "image-shapes",
  name: "Image QA fixtures",
  target_count: 1,
  reviewer_id: "qa",
  current_position: 0,
  current_filter: "pending",
  state: "open",
  confirmed: 0,
  replaced: 0,
  kept: 0,
  skipped: 0,
  done: 0,
  remaining: 1,
  selected_sources: {},
  items: [{ position: 0, state: "pending", attention: false }],
};
let rank = 1;
(async () => {
  const browser = await chromium.launch({ headless: true });
  for (const [mode, width, height] of [
    ["desktop", 1366, 900],
    ["mobile", 390, 844],
  ]) {
    const page = await browser.newPage({ viewport: { width, height } });
    await page.route("**/api/v1/**", async (route) => {
      const url = route.request().url();
      const match = url.match(/shape-(\d+)\/content/);
      if (match) {
        const [w, h] = shapes[+match[1] - 1];
        return route.fulfill({
          contentType: "image/svg+xml",
          body: `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><rect width="${w}" height="${h}" fill="#b9d4df"/><rect x="3" y="3" width="${w - 6}" height="${h - 6}" fill="none" stroke="#e43443" stroke-width="6"/><text x="${w / 2}" y="${h / 2}" text-anchor="middle" font-size="45">${w} × ${h}</text></svg>`,
        });
      }
      let data = summary;
      if (url.endsWith("/photo-reviewers"))
        data = [{ id: "qa", display_name: "QA" }];
      if (url.endsWith("/action")) {
        const d = route.request().postDataJSON();
        assert.equal(d.action, "select");
        rank = d.rank;
      }
      if (/\/items\/0$/.test(url))
        data = {
          position: 0,
          selection_id: "shape-selection",
          confirmed_at: null,
          state: "pending",
          automatic_rank: 1,
          active_rank: rank,
          community: "Пять форматов · fixture",
          category: "QA",
          intent: "Полный кадр",
          attention: [],
          neighbors: [],
          candidates: shapes.map((_, i) => ({
            rank: i + 1,
            provider: "library",
            image: `/api/v1/media-assets/shape-${i + 1}/content`,
            rating: null,
            diagnostics: {},
          })),
        };
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(data),
      });
    });
    await page.goto(base + "/review/image-shapes");
    await page.locator(".validation-photo img").waitFor();
    await page
      .getByRole("button", { name: "Альтернативы", exact: true })
      .click();
    for (let i = 0; i < 5; i++) {
      await page.locator(".validation-alternatives button").nth(i).click();
      await page.waitForFunction(
        (r) =>
          document
            .querySelector(".validation-photo img")
            ?.src.includes("shape-" + r + "/"),
        i + 1,
      );
      await page
        .locator(".validation-photo img")
        .evaluate((img) => img.decode());
      const geometry = await page
        .locator(".validation-photo img")
        .evaluate((img) => {
          const r = img.getBoundingClientRect(),
            p = img.parentElement.getBoundingClientRect(),
            c = getComputedStyle(img);
          return {
            width: r.width,
            height: r.height,
            parentWidth: p.width,
            parentHeight: p.height,
            naturalWidth: img.naturalWidth,
            naturalHeight: img.naturalHeight,
            fit: c.objectFit,
          };
        });
      assert(
        geometry.width <= geometry.parentWidth + 1 &&
          geometry.height <= geometry.parentHeight + 1,
        JSON.stringify(geometry),
      );
      assert(
        Math.abs(
          geometry.width / geometry.height - shapes[i][0] / shapes[i][1],
        ) < 0.02,
        JSON.stringify(geometry),
      );
      assert.equal(geometry.fit, "contain");
      assert(
        await page
          .locator(".validation-alternatives img")
          .evaluateAll((es) =>
            es.every((e) => getComputedStyle(e).objectFit === "contain"),
          ),
      );
      assert(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      );
      await page.screenshot({
        path: `${out}/${mode}-${i + 1}.png`,
        fullPage: true,
      });
      await page.locator(".validation-photo").click();
      await page.locator(".validation-overlay img").waitFor();
      const full = await page
        .locator(".validation-overlay img")
        .evaluate((img) => ({
          w: img.clientWidth,
          h: img.clientHeight,
          nw: img.naturalWidth,
          nh: img.naturalHeight,
          fit: getComputedStyle(img).objectFit,
          vw: innerWidth,
          vh: innerHeight,
        }));
      assert(full.w <= full.vw * 0.95 + 1 && full.h <= full.vh * 0.9 + 1);
      assert.equal(full.fit, "contain");
      assert(Math.abs(full.w / full.h - full.nw / full.nh) < 0.03);
      await page.screenshot({ path: `${out}/${mode}-${i + 1}-fullscreen.png` });
      await page.keyboard.press("Escape");
    }
    console.log(
      `${mode}: all five shapes preserve frame/aspect ratio in primary, alternatives and fullscreen; no overflow`,
    );
    await page.close();
  }
  await browser.close();
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
