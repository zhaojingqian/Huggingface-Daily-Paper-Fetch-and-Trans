#!/usr/bin/env bash
# 定时清理 gpt-academic-latex-slim 容器内的过期翻译缓存。
# 与全文翻译共用全局锁；翻译繁忙时直接跳过，绝不触碰活跃 workfolder。

set -u

ROOT="${PAPER_TRANS_ROOT:-/root/workspace/paper-trans}"
LOG="${PAPER_TRANS_CLEANUP_LOG:-${ROOT}/logs/cleanup.log}"
LOCK_FILE="${PAPER_TRANS_FULL_TRANSLATION_LOCK:-${ROOT}/locks/full-translation.lock}"
CONTAINER="${GPT_ACADEMIC_CONTAINER:-gpt-academic-latex-slim}"
RETENTION_DAYS="${PAPER_TRANS_CACHE_RETENTION_DAYS:-30}"
DOCKER_TIMEOUT="${PAPER_TRANS_DOCKER_CONTROL_TIMEOUT:-60}"

log() {
    printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG"
}

mkdir -p "$(dirname "$LOG")" "$(dirname "$LOCK_FILE")"

case "$RETENTION_DAYS" in
    ''|*[!0-9]*|0)
        log "[ERROR] PAPER_TRANS_CACHE_RETENTION_DAYS 必须是正整数: ${RETENTION_DAYS}"
        exit 2
        ;;
esac

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

host_bounded() {
    timeout --signal=TERM --kill-after=5s "${DOCKER_TIMEOUT}s" "$@"
}

# 不截断锁文件：翻译进程会在其中记录 pid/arxiv_id，维护脚本只共享 inode 锁。
exec 9>>"$LOCK_FILE"
if ! flock -n 9; then
    log "[SKIP] 全文翻译繁忙（锁 ${LOCK_FILE}），本轮缓存清理跳过"
    exit 0
fi

log "=== 开始清理过期容器缓存（保留 ${RETENTION_DAYS} 天）==="

RUNNING="$(docker_bounded container inspect -f '{{.State.Running}}' "$CONTAINER" 2>&1)"
RUNNING_STATUS=$?
if [ "$RUNNING_STATUS" -ne 0 ]; then
    if [ "$RUNNING_STATUS" -eq 124 ] || [ "$RUNNING_STATUS" -eq 137 ]; then
        log "[ERROR] 检查容器 ${CONTAINER} 状态超时（${DOCKER_TIMEOUT}s）"
    else
        log "[ERROR] 检查容器 ${CONTAINER} 状态失败: ${RUNNING}"
    fi
    exit 1
fi
if [ "$RUNNING" != "true" ]; then
    log "[SKIP] 容器 ${CONTAINER} 未运行，跳过清理"
    exit 0
fi

BEFORE_RAW="$(docker_bounded exec "$CONTAINER" du -sh /gpt/gpt_log 2>&1)"
BEFORE_STATUS=$?
if [ "$BEFORE_STATUS" -eq 0 ]; then
    BEFORE="$(printf '%s\n' "$BEFORE_RAW" | awk 'NR == 1 {print $1}')"
else
    BEFORE="unknown"
    log "[WARN] 清理前容器磁盘统计失败或超时（status=${BEFORE_STATUS}）"
fi
[ -n "$BEFORE" ] || BEFORE="unknown"
DISK_BEFORE_RAW="$(host_bounded df -P "$ROOT" 2>/dev/null)"
DISK_BEFORE_STATUS=$?
if [ "$DISK_BEFORE_STATUS" -eq 0 ]; then
    DISK_BEFORE="$(printf '%s\n' "$DISK_BEFORE_RAW" | awk 'NR == 2 {print $5}')"
else
    DISK_BEFORE="unknown"
    log "[WARN] 清理前宿主磁盘统计失败或超时（status=${DISK_BEFORE_STATUS}）"
fi
[ -n "$DISK_BEFORE" ] || DISK_BEFORE="unknown"
log "[INFO] 清理前：gpt_log=${BEFORE}，磁盘使用=${DISK_BEFORE}"

# 只考虑各缓存根目录的一级条目。目录必须自身已过期，且内部不存在保留期内
# 更新过的文件，才会被递归删除；缓存根目录本身永远保留。删除命令在容器
# 内也设超时，避免宿主 docker 客户端超时后容器内 find 继续脱锁运行。
CLIENT_TIMEOUT=$((10#$DOCKER_TIMEOUT + 6))
CLEAN_OUTPUT="$(
    timeout --signal=TERM --kill-after=5s "${CLIENT_TIMEOUT}s" \
    docker exec "$CONTAINER" \
    timeout --signal=TERM --kill-after=5s "${DOCKER_TIMEOUT}s" \
    sh -c '
        set -eu
        retention_days="$1"
        for root in \
            /gpt/gpt_log/arxiv_cache \
            /gpt/gpt_log/default_user \
            /gpt/gpt_log/admin
        do
            [ -d "$root" ] || continue

            find "$root" -mindepth 1 -maxdepth 1 \
                \( -type f -o -type l \) \
                -mtime "+$retention_days" \
                -printf "DELETE %p\n" -delete

            old_dirs="$(
                find "$root" -mindepth 1 -maxdepth 1 -type d \
                    -mtime "+$retention_days" -print
            )"
            printf "%s\n" "$old_dirs" |
            while IFS= read -r entry
            do
                [ -n "$entry" ] || continue
                recent="$(
                    find "$entry" -type f -mtime "-$retention_days" \
                        -print -quit
                )"
                if [ -n "$recent" ]; then
                    printf "KEEP_RECENT %s\n" "$entry"
                    continue
                fi
                printf "DELETE %s\n" "$entry"
                find "$entry" -depth -delete
            done
        done
    ' sh "$RETENTION_DAYS" 2>&1
)"
CLEAN_STATUS=$?
if [ "$CLEAN_STATUS" -ne 0 ]; then
    if [ "$CLEAN_STATUS" -eq 124 ] || [ "$CLEAN_STATUS" -eq 137 ]; then
        log "[ERROR] 容器缓存清理超时（${DOCKER_TIMEOUT}s）: ${CLEAN_OUTPUT}"
    else
        log "[ERROR] 容器缓存清理失败（status=${CLEAN_STATUS}）: ${CLEAN_OUTPUT}"
    fi
    exit 1
fi

if [ -n "$CLEAN_OUTPUT" ]; then
    printf '%s\n' "$CLEAN_OUTPUT" >> "$LOG"
fi
DELETED_COUNT="$(
    printf '%s\n' "$CLEAN_OUTPUT" |
        awk '/^DELETE / {count += 1} END {print count + 0}'
)"
KEPT_COUNT="$(
    printf '%s\n' "$CLEAN_OUTPUT" |
        awk '/^KEEP_RECENT / {count += 1} END {print count + 0}'
)"

AFTER_RAW="$(docker_bounded exec "$CONTAINER" du -sh /gpt/gpt_log 2>&1)"
AFTER_STATUS=$?
if [ "$AFTER_STATUS" -eq 0 ]; then
    AFTER="$(printf '%s\n' "$AFTER_RAW" | awk 'NR == 1 {print $1}')"
else
    AFTER="unknown"
    log "[WARN] 清理后容器磁盘统计失败或超时（status=${AFTER_STATUS}）"
fi
[ -n "$AFTER" ] || AFTER="unknown"
DISK_AFTER_RAW="$(host_bounded df -P "$ROOT" 2>/dev/null)"
DISK_AFTER_STATUS=$?
if [ "$DISK_AFTER_STATUS" -eq 0 ]; then
    DISK_AFTER="$(printf '%s\n' "$DISK_AFTER_RAW" | awk 'NR == 2 {print $5}')"
else
    DISK_AFTER="unknown"
    log "[WARN] 清理后宿主磁盘统计失败或超时（status=${DISK_AFTER_STATUS}）"
fi
[ -n "$DISK_AFTER" ] || DISK_AFTER="unknown"
log "[INFO] 清理后：gpt_log=${AFTER}，磁盘使用=${DISK_AFTER}，删除=${DELETED_COUNT}，保留近期=${KEPT_COUNT}"
log "=== 清理完成 ==="
