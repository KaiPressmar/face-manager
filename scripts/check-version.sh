#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

node - "${PROJECT_ROOT}" <<'JS'
const fs = require("fs");
const path = require("path");

const root = process.argv[2];
const version = fs.readFileSync(path.join(root, "VERSION"), "utf8").trim();
const packageJson = JSON.parse(
  fs.readFileSync(path.join(root, "frontend/package.json"), "utf8")
);
const packageLock = JSON.parse(
  fs.readFileSync(path.join(root, "frontend/package-lock.json"), "utf8")
);
const semver = /^\d+\.\d+\.\d+$/;

if (!semver.test(version)) {
  throw new Error(`VERSION is not semantic: ${version}`);
}
if (packageJson.version !== version) {
  throw new Error(
    `frontend/package.json is ${packageJson.version}, expected ${version}`
  );
}
if (packageLock.version !== version) {
  throw new Error(
    `frontend/package-lock.json is ${packageLock.version}, expected ${version}`
  );
}
if (packageLock.packages?.[""]?.version !== version) {
  throw new Error("Root package-lock entry does not match VERSION");
}

console.log(`Version metadata is consistent: ${version}`);
JS
