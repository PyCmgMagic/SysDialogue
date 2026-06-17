#!/usr/bin/env node
// bin/sysdialogue.js — sysdialogue CLI/TUI launcher

const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");
const os = require("os");

const isWindows = os.platform() === "win32";
const PACKAGE_ROOT = path.resolve(__dirname, "..");
const VENV_DIR = path.join(PACKAGE_ROOT, ".venv");
const MARKER = path.join(VENV_DIR, ".sysdialogue-installed");

const red = (s) => `\x1b[31m${s}\x1b[0m`;
const cyan = (s) => `\x1b[36m${s}\x1b[0m`;

function log(msg) {
  console.log(`  ${cyan("[sysdialogue]")}`, msg);
}

function error(msg) {
  console.error(`  ${red("[sysdialogue]")}`, msg);
}

if (!fs.existsSync(MARKER)) {
  error("Python environment not installed! Re-run: npm install -g sysdialogue");
  process.exit(1);
}

const pythonBin = isWindows
  ? path.join(VENV_DIR, "Scripts", "python.exe")
  : path.join(VENV_DIR, "bin", "python");

if (!fs.existsSync(pythonBin)) {
  error(`Python binary not found: ${pythonBin}`);
  error("Please reinstall: npm install -g sysdialogue");
  process.exit(1);
}

const args = ["-m", "sysdialogue.app.cli", ...process.argv.slice(2)];

const child = spawn(pythonBin, args, {
  stdio: "inherit",
  cwd: process.cwd(),
  env: { ...process.env },
});

child.on("exit", (code) => {
  process.exit(code ?? 0);
});

child.on("error", (err) => {
  error(`Failed to start: ${err.message}`);
  process.exit(1);
});
