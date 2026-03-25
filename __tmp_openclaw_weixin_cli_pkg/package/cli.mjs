#!/usr/bin/env node

import { execSync, spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import {
  COMPAT_MATRIX,
  findCompatEntry,
  formatRange,
} from "./lib/compat.mjs";

const PLUGIN_SPEC = "@tencent-weixin/openclaw-weixin";
const CHANNEL_ID = "openclaw-weixin";

// ── helpers ──────────────────────────────────────────────────────────────────

function log(msg) {
  console.log(`\x1b[36m[openclaw-weixin]\x1b[0m ${msg}`);
}

function error(msg) {
  console.error(`\x1b[31m[openclaw-weixin]\x1b[0m ${msg}`);
}

function run(cmd, { silent = true } = {}) {
  const stdio = silent ? ["pipe", "pipe", "pipe"] : "inherit";
  const result = spawnSync(cmd, { shell: true, stdio });
  if (result.status !== 0) {
    const err = new Error(`Command failed with exit code ${result.status}: ${cmd}`);
    err.stderr = silent ? (result.stderr || "").toString() : "";
    throw err;
  }
  return silent ? (result.stdout || "").toString().trim() : "";
}

function which(bin) {
  const cmd = process.platform === "win32" ? `where ${bin}` : `which ${bin}`;
  try {
    return execSync(cmd, { encoding: "utf-8", stdio: ["pipe", "pipe", "pipe"] }).trim();
  } catch {
    return null;
  }
}

// ── version detection ───────────────────────────────────────────────────────

function getOpenclawVersion() {
  try {
    const raw = run("openclaw --version");
    const match = raw.match(/(\d+\.\d+\.\d+)/);
    return match ? match[1] : null;
  } catch {
    return null;
  }
}

function selectPluginTag(openclawVersion) {
  const entry = findCompatEntry(openclawVersion);
  if (entry) return entry;

  error(`当前 OpenClaw 版本 ${openclawVersion} 不在任何已知兼容范围内`);
  console.log("\n  已知兼容矩阵:");
  for (const e of COMPAT_MATRIX) {
    console.log(`    ${e.label}  →  OpenClaw ${formatRange(e.openclawRange)}`);
  }
  console.log();
  return null;
}

// ── symlink ──────────────────────────────────────────────────────────────────

/**
 * Resolve the host openclaw package root from the `openclaw` binary.
 * e.g. /Users/x/.nvm/versions/node/v22/lib/node_modules/openclaw
 */
function resolveHostOpenclawRoot() {
  const bin = which("openclaw");
  if (!bin) return null;
  try {
    // Follow symlinks to the real binary, then go up to the package root.
    const real = fs.realpathSync(bin);
    // binary is at <root>/openclaw.mjs or <root>/dist/index.js etc.
    // Walk up until we find a package.json with name "openclaw".
    let dir = path.dirname(real);
    for (let i = 0; i < 6; i++) {
      try {
        const pkg = JSON.parse(fs.readFileSync(path.join(dir, "package.json"), "utf-8"));
        if (pkg.name === "openclaw") return dir;
      } catch {}
      const parent = path.dirname(dir);
      if (parent === dir) break;
      dir = parent;
    }
  } catch {}
  return null;
}

/**
 * Resolve the plugin extensions directory.
 * Default: ~/.openclaw/extensions/openclaw-weixin
 */
function resolvePluginExtDir() {
  const stateDir = process.env.OPENCLAW_STATE_DIR || path.join(os.homedir(), ".openclaw");
  return path.join(stateDir, "extensions", "openclaw-weixin");
}

/**
 * Create a symlink from the plugin's node_modules/openclaw to the host
 * openclaw package root. This lets jiti resolve openclaw/plugin-sdk/*
 * without openclaw being a runtime dependency.
 */
function ensureOpenclawSymlink() {
  const hostRoot = resolveHostOpenclawRoot();
  if (!hostRoot) {
    error("无法定位宿主 openclaw 包根目录，跳过 symlink 创建");
    return;
  }

  const pluginDir = resolvePluginExtDir();
  if (!fs.existsSync(pluginDir)) {
    // Plugin not in extensions dir (might be a -l link install); skip.
    return;
  }

  const nmDir = path.join(pluginDir, "node_modules");
  const linkPath = path.join(nmDir, "openclaw");

  // Check if already correct
  try {
    const existing = fs.readlinkSync(linkPath);
    if (fs.realpathSync(existing) === fs.realpathSync(hostRoot)) {
      log("openclaw symlink 已存在且正确");
      return;
    }
    // Wrong target — remove and recreate
    fs.unlinkSync(linkPath);
  } catch {
    // Not a symlink or doesn't exist — fine
  }

  fs.mkdirSync(nmDir, { recursive: true });
  fs.symlinkSync(hostRoot, linkPath);
  log(`已创建 symlink: node_modules/openclaw → ${hostRoot}`);
}

// ── commands ─────────────────────────────────────────────────────────────────

function install() {
  // 1. Check openclaw is installed
  if (!which("openclaw")) {
    error("未找到 openclaw，请先安装：");
    console.log("  npm install -g openclaw");
    console.log("  详见 https://docs.openclaw.ai/install");
    process.exit(1);
  }
  log("已找到本地安装的 openclaw");

  // 2. Detect host version and select compatible plugin
  const hostVersion = getOpenclawVersion();
  if (!hostVersion) {
    error("无法获取 openclaw 版本号，请确认 `openclaw --version` 正常工作");
    process.exit(1);
  }
  log(`检测到 OpenClaw 版本: ${hostVersion}`);

  const compat = selectPluginTag(hostVersion);
  if (!compat) {
    process.exit(1);
  }

  const pluginInstallSpec = `${PLUGIN_SPEC}@${compat.distTag}`;
  log(`匹配兼容版本: ${compat.label} (dist-tag: ${compat.distTag})`);

  // 3. Install plugin via openclaw
  log(`正在安装插件 ${pluginInstallSpec}...`);
  try {
    const installOut = run(`openclaw plugins install "${pluginInstallSpec}"`);
    if (installOut) log(installOut);
  } catch (installErr) {
    if (installErr.stderr && installErr.stderr.includes("already exists")) {
      log("检测到本地已安装，正在更新...");
      try {
        const updateOut = run(`openclaw plugins update "${CHANNEL_ID}"`);
        if (updateOut) log(updateOut);
      } catch (updateErr) {
        error("插件更新失败，请手动执行：");
        if (updateErr.stderr) console.error(updateErr.stderr);
        console.log(`  openclaw plugins update "${CHANNEL_ID}"`);
        process.exit(1);
      }
    } else {
      error("插件安装失败，请手动执行：");
      if (installErr.stderr) console.error(installErr.stderr);
      console.log(`  openclaw plugins install "${pluginInstallSpec}"`);
      process.exit(1);
    }
  }

  // 4. Symlink host openclaw into plugin's node_modules
  //    so jiti can resolve openclaw/plugin-sdk/* without a runtime dependency.
  ensureOpenclawSymlink();

  // 5. Login (interactive QR scan)
  log("插件就绪，开始首次连接...");
  try {
    run(`openclaw channels login --channel ${CHANNEL_ID}`, { silent: false });
  } catch {
    console.log();
    error("首次连接未完成，可稍后手动重试：");
    console.log(`  openclaw channels login --channel ${CHANNEL_ID}`);
  }

  // 6. Restart gateway so it picks up the new account
  log("正在重启 OpenClaw Gateway...");
  try {
    run(`openclaw gateway restart`, { silent: false });
  } catch {
    error("重启失败，可手动执行：");
    console.log(`  openclaw gateway restart`);
  }
}

function help() {
  console.log(`
  用法: npx -y @tencent-weixin/openclaw-weixin-cli <命令>

  命令:
    install   自动检测 OpenClaw 版本，安装兼容的微信插件并扫码连接
    help      显示帮助信息

  兼容矩阵:`);
  for (const e of COMPAT_MATRIX) {
    console.log(`    ${e.label.padEnd(28)} OpenClaw ${formatRange(e.openclawRange)}`);
  }
  console.log();
}

// ── main ─────────────────────────────────────────────────────────────────────

const command = process.argv[2];

switch (command) {
  case "install":
    install();
    break;
  case "help":
  case "--help":
  case "-h":
    help();
    break;
  default:
    if (command) {
      error(`未知命令: ${command}`);
    }
    help();
    process.exit(command ? 1 : 0);
}
