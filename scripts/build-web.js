#!/usr/bin/env node
// scripts/build-web.js — clean build the web frontend
const fs = require("fs");
const path = require("path");
const { execSync } = require("child_process");

const webDir = path.resolve(__dirname, "..", "web");
const distDir = path.join(webDir, "dist");

// Clean dist directory
if (fs.existsSync(distDir)) {
  fs.rmSync(distDir, { recursive: true, force: true });
  console.log("  [build-web] Cleaned dist/");
}

// Build
console.log("  [build-web] Building...");
execSync("node node_modules/vite/bin/vite.js build --configLoader runner", {
  cwd: webDir,
  stdio: "inherit",
});

console.log("  [build-web] Done.");

// Fix LF line endings for bin/scripts (npm requires LF shebang)
const rootDir = path.resolve(__dirname, "..");
const jsFiles = [
  "bin/sysdialogue.js",
  "bin/sysdialogue-web.js",
  "scripts/postinstall.js",
  "scripts/preuninstall.js",
];
for (const f of jsFiles) {
  const fp = path.join(rootDir, f);
  if (fs.existsSync(fp)) {
    let content = fs.readFileSync(fp, "utf8");
    if (content.includes("\r\n")) {
      content = content.replace(/\r\n/g, "\n");
      fs.writeFileSync(fp, content, "utf8");
      console.log(`  [build-web] Fixed LF: ${f}`);
    }
  }
}
