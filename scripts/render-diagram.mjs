// A fresh, offline browser per render; no user profile, CDN, or diagram actions.
import { createHash } from "node:crypto";
import { existsSync, readFileSync, realpathSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import puppeteer from "puppeteer";
import { renderMermaid } from "@mermaid-js/mermaid-cli";

const root = realpathSync(path.resolve(path.dirname(fileURLToPath(import.meta.url)), ".."));
const within = (base, candidate) => {
  const relative = path.relative(base, candidate);
  return relative !== ".." && !relative.startsWith(".." + path.sep) && !path.isAbsolute(relative);
};
const hash = (bytes) => createHash("sha256").update(bytes).digest("hex");
const [input, output] = process.argv.slice(2);
if (!input || !output || !within(root, path.resolve(input)) || !within(root, path.resolve(output))) {
  throw new Error("Input and output must be project-local paths");
}
const definition = readFileSync(input, "utf8");
if (Buffer.byteLength(definition) > 100000 || /%%\s*\{|^\s*---|\b(?:https?|file|data|javascript)\s*:|<\s*\/?\s*[a-z][^>]*>|^\s*click\b|\b(?:img|icon)\s*:|@import|url\s*\(/im.test(definition)) {
  throw new Error("Diagram contains unsupported config, HTML, actions or resource URLs");
}
const packageVersion = (name) => JSON.parse(readFileSync(path.join(root, "node_modules", name, "package.json"))).version;
for (const [name, version] of [["@mermaid-js/mermaid-cli", "12.0.0"], ["@fontsource/noto-sans-kr", "5.3.0"], ["puppeteer", "25.12.0"]]) {
  if (packageVersion(name) !== version) throw new Error(`Unexpected ${name} version; run npm ci --ignore-scripts`);
}
const candidates = [
  process.env.CS_STUDY_CHROMIUM_PATH,
  process.env.PROGRAMFILES && path.join(process.env.PROGRAMFILES, "Google/Chrome/Application/chrome.exe"),
  process.env["PROGRAMFILES(X86)"] && path.join(process.env["PROGRAMFILES(X86)"], "Microsoft/Edge/Application/msedge.exe"),
  "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
].filter(Boolean);
const executablePath = candidates.find((candidate) => existsSync(candidate));
if (!executablePath) throw new Error("No local Chromium browser; set CS_STUDY_CHROMIUM_PATH");
const mermaidConfig = {
  securityLevel: "strict",
  startOnLoad: false,
  deterministicIds: true,
  deterministicIDSeed: hash(definition),
  theme: "neutral",
  fontFamily: "Noto Sans KR",
  htmlLabels: false,
  flowchart: { htmlLabels: false, useMaxWidth: false },
  sequence: { useMaxWidth: false },
};
let browser;
let timeout;
try {
  browser = await puppeteer.launch({
    executablePath,
    headless: true,
    timeout: 20000,
    args: ["--disable-background-networking", "--disable-component-update", "--no-first-run", "--disable-gpu", "--force-color-profile=srgb", "--host-resolver-rules=MAP * ~NOTFOUND"],
  });
  const browserVersion = await browser.version();
  const modules = realpathSync(path.join(root, "node_modules"));
  const guardedBrowser = {
    async newPage() {
      const page = await browser.newPage();
      await page.setRequestInterception(true);
      page.on("request", (request) => {
        if (request.isInterceptResolutionHandled()) return;
        const url = new URL(request.url());
        if (url.origin === "https://mermaid-cli-intercept.invalid") {
          // Served by Mermaid's local interceptor, never sent to the network.
          return;
        }
        if (url.protocol === "data:" || url.protocol === "about:") {
          return request.continue({}, -1);
        }
        if (url.protocol === "file:") {
          try {
            if (within(modules, realpathSync(fileURLToPath(url)))) return request.continue({}, -1);
          } catch { /* Unknown files stay blocked. */ }
        }
        return request.abort("blockedbyclient", 100);
      });
      return page;
    },
  };
  const { data } = await Promise.race([
    renderMermaid(guardedBrowser, definition, "png", {
      viewport: { width: 1400, height: 1000, deviceScaleFactor: 2 },
      backgroundColor: "white",
      mermaidConfig,
      customFontCSS: [400, 700].map((weight) => ({ cssUrl: pathToFileURL(path.join(modules, "@fontsource/noto-sans-kr", `${weight}.css`)) })),
      fontEmbed: true,
      svgId: "study-diagram",
    }),
    new Promise((_, reject) => { timeout = setTimeout(() => reject(new Error("Rendering exceeded 60 seconds")), 60000); }),
  ]);
  writeFileSync(output, data, { flag: "wx" });
  process.stdout.write(JSON.stringify({
    mermaidCli: packageVersion("@mermaid-js/mermaid-cli"),
    mermaid: packageVersion("mermaid"),
    puppeteer: packageVersion("puppeteer"),
    font: packageVersion("@fontsource/noto-sans-kr"),
    node: process.version,
    browser: browserVersion,
    platform: process.platform,
    architecture: process.arch,
    lockHash: hash(readFileSync(path.join(root, "package-lock.json"))),
    scriptHash: hash(readFileSync(fileURLToPath(import.meta.url))),
    config: { ...mermaidConfig, deterministicIDSeed: "<spec-sha256>" },
  }) + "\n");
} finally {
  clearTimeout(timeout);
  if (browser) await browser.close();
}
