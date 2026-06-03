#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

if [[ -z "${WVP_JAR:-}" ]]; then
  WVP_JAR="$(find "${PROJECT_ROOT}/wvp-GB28181-pro/target" -maxdepth 1 -type f -name 'wvp-pro-*.jar' 2>/dev/null | sort | tail -n 1 || true)"
fi
if [[ -z "${WVP_JAR:-}" ]]; then
  WVP_JAR="$(find "${PROJECT_ROOT}/release-wvp" -maxdepth 1 -type f -name 'wvp-pro-*.jar' 2>/dev/null | sort | tail -n 1 || true)"
fi
if [[ -z "${WVP_JAR:-}" ]]; then
  WVP_JAR="${PROJECT_ROOT}/wvp-GB28181-pro/target/wvp-pro-2.7.4.jar"
fi
WVP_CONFIG="${WVP_CONFIG:-${SCRIPT_DIR}/application-safehat.yml}"
if [[ -n "${JAVA_BIN:-}" ]]; then
  JAVA_BIN="${JAVA_BIN}"
elif [[ -x "${PROJECT_ROOT}/jdk-21.0.11/bin/java" ]]; then
  JAVA_BIN="${PROJECT_ROOT}/jdk-21.0.11/bin/java"
else
  JAVA_BIN="java"
fi
WVP_LOG="${WVP_LOG:-${PROJECT_ROOT}/logs/wvp.log}"

if [[ ! -f "${WVP_JAR}" ]]; then
  echo "WVP jar not found: ${WVP_JAR}"
  echo "Set WVP_JAR=/path/to/wvp.jar or build wvp-GB28181-pro first."
  exit 1
fi

if [[ ! -f "${WVP_CONFIG}" ]]; then
  echo "WVP config not found: ${WVP_CONFIG}"
  exit 1
fi

mkdir -p "$(dirname "${WVP_LOG}")"
echo "[WVP] jar: ${WVP_JAR}"
echo "[WVP] config: ${WVP_CONFIG}"
echo "[WVP] log: ${WVP_LOG}"

exec "${JAVA_BIN}" \
  -Xms256m \
  -Xmx768m \
  -Dfile.encoding=UTF-8 \
  -jar "${WVP_JAR}" \
  --spring.config.additional-location="file:${WVP_CONFIG}" \
  >> "${WVP_LOG}" 2>&1
