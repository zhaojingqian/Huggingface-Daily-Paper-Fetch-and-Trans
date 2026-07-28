#!/usr/bin/env bash
# 仅在没有全文翻译占用共享容器时执行安全重启。

set -u

ROOT="${PAPER_TRANS_ROOT:-/root/workspace/paper-trans}"
LOG="${PAPER_TRANS_RESTART_LOG:-${ROOT}/logs/container-restart.log}"
LOCK_FILE="${PAPER_TRANS_FULL_TRANSLATION_LOCK:-${ROOT}/locks/full-translation.lock}"
CONTAINER="${GPT_ACADEMIC_CONTAINER:-gpt-academic-latex-slim}"
DOCKER_TIMEOUT="${PAPER_TRANS_DOCKER_CONTROL_TIMEOUT:-60}"

log() {
    printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG"
}

mkdir -p "$(dirname "$LOG")" "$(dirname "$LOCK_FILE")"

case "$DOCKER_TIMEOUT" in
    ''|*[!0-9]*|0)
        log "[ERROR] PAPER_TRANS_DOCKER_CONTROL_TIMEOUT 必须是正整数秒: ${DOCKER_TIMEOUT}"
        exit 2
        ;;
esac
if [ "${#DOCKER_TIMEOUT}" -gt 3 ] || [ "$DOCKER_TIMEOUT" -gt 600 ]; then
    log "[ERROR] PAPER_TRANS_DOCKER_CONTROL_TIMEOUT 不能超过 600 秒: ${DOCKER_TIMEOUT}"
    exit 2
fi

docker_bounded() {
    timeout --signal=TERM --kill-after=5s "${DOCKER_TIMEOUT}s" docker "$@"
}

# 非阻塞获取全文翻译锁。维护任务不能排队后突然重启正在使用的容器。
exec 9>>"$LOCK_FILE"
if ! flock -n 9; then
    log "[SKIP] 全文翻译繁忙（锁 ${LOCK_FILE}），容器 ${CONTAINER} 不重启"
    exit 0
fi

log "[INFO] 全文翻译空闲，开始重启容器 ${CONTAINER}"
OUTPUT="$(docker_bounded restart "$CONTAINER" 2>&1)"
STATUS=$?
if [ "$STATUS" -eq 0 ]; then
    [ -z "$OUTPUT" ] || log "[INFO] docker: ${OUTPUT}"
    log "[OK] 容器 ${CONTAINER} 重启完成"
    exit 0
fi

if [ "$STATUS" -eq 124 ] || [ "$STATUS" -eq 137 ]; then
    log "[ERROR] 容器 ${CONTAINER} 重启超时（${DOCKER_TIMEOUT}s）: ${OUTPUT}"
    exit 1
fi
log "[ERROR] 容器 ${CONTAINER} 重启失败: ${OUTPUT}"
exit 1
