#!/usr/bin/env node
// preuninstall.js — 卸载前清理 Python venv

const fs = require("fs");
const path = require("path");
const os = require("os");
const { execSync } = require("child_process");

const isWindows = os.platform() === "win32";
const PACKAGE_ROOT = path.resolve(__dirname, "..");
const VENV_DIR = path.join(PACKAGE_ROOT, ".venv");

function log(msg) {
  console.log(`  \x1b[36m[sysdialogue]\x1b[0m`, msg);
}

try {
  if (fs.existsSync(VENV_DIR)) {
    log("清理 Python 虚拟环境...");
    fs.rmSync(VENV_DIR, { recursive: true, force: true });
    log("虚拟环境已清理。");
  }
} catch (e) {
  // 忽略清理错误
}
