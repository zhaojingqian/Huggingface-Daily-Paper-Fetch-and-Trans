#!/usr/bin/env bash
# 定时清理 gpt-academic-latex-slim 容器内的过期翻译缓存。
# 与全文翻译共用全局锁；翻译繁忙时直接跳过，绝不触碰活跃 workfolder。

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/load_workspace_env.sh
source "${SCRIPT_DIR}/load_workspace_env.sh"
ROOT="${PAPER_TRANS_ROOT:-$(dirname "$SCRIPT_DIR")}"
LOG="${PAPER_TRANS_CLEANUP_LOG:-${ROOT}/logs/cleanup.log}"
LOCK_FILE="${PAPER_TRANS_FULL_TRANSLATION_LOCK:-${ROOT}/locks/full-translation.lock}"
CONTAINER="${GPT_ACADEMIC_CONTAINER:-gpt-academic-latex-slim}"
RETENTION_DAYS="${PAPER_TRANS_CACHE_RETENTION_DAYS:-3}"
EMERGENCY_RETENTION_DAYS="${PAPER_TRANS_EMERGENCY_RETENTION_DAYS:-1}"
DISK_HIGH_WATERMARK="${PAPER_TRANS_DISK_HIGH_WATERMARK:-90}"
DISK_CRITICAL_WATERMARK="${PAPER_TRANS_DISK_CRITICAL_WATERMARK:-95}"
MIN_FREE_MB="${PAPER_TRANS_MIN_FREE_MB:-2048}"
EPHEMERAL_GRACE_MINUTES="${PAPER_TRANS_EPHEMERAL_GRACE_MINUTES:-120}"
DOCKER_TIMEOUT="${PAPER_TRANS_DOCKER_CONTROL_TIMEOUT:-60}"
PYTHON="$PAPER_TRANS_PYTHON"
ALERT_SCRIPT="${ROOT}/scripts/send_maintenance_alert.py"
ALERT_CONFIG="${PAPER_TRANS_ALERT_CONFIG:-/root/scholar-citation-monitor/config.env}"
RECLAIM_HELPER="${ROOT}/scripts/reclaim_translation_cache.py"
ALERT_ATTEMPTED=0

log() {
    printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG"
}

alert() {
    subject="$1"
    body="$2"
    ALERT_ATTEMPTED=1
    if [ ! -f "$ALERT_SCRIPT" ]; then
        log "[WARN] 告警脚本不存在: ${ALERT_SCRIPT}"
        return 1
    fi
    if "$PYTHON" "$ALERT_SCRIPT" --config "$ALERT_CONFIG" \
        --subject "$subject" --body "$body" >/dev/null 2>&1; then
        log "[ALERT] Gmail 告警已发送: ${subject}"
        return 0
    fi
    log "[WARN] Gmail 告警发送失败: ${subject}"
    return 1
}

on_exit() {
    status=$?
    trap - EXIT
    if [ "$status" -ne 0 ] && [ "$ALERT_ATTEMPTED" -eq 0 ]; then
        alert "缓存清理失败" \
            "缓存清理进程异常退出（exit=${status}）。请检查 ${LOG}。" || true
    fi
    exit "$status"
}
trap on_exit EXIT

mkdir -p "$(dirname "$LOG")" "$(dirname "$LOCK_FILE")"

case "$RETENTION_DAYS" in
    ''|*[!0-9]*|0)
        log "[ERROR] PAPER_TRANS_CACHE_RETENTION_DAYS 必须是正整数: ${RETENTION_DAYS}"
        exit 2
        ;;
esac

for setting in \
    "EMERGENCY_RETENTION_DAYS:$EMERGENCY_RETENTION_DAYS" \
    "DISK_HIGH_WATERMARK:$DISK_HIGH_WATERMARK" \
    "DISK_CRITICAL_WATERMARK:$DISK_CRITICAL_WATERMARK" \
    "MIN_FREE_MB:$MIN_FREE_MB" \
    "EPHEMERAL_GRACE_MINUTES:$EPHEMERAL_GRACE_MINUTES"
do
    name="${setting%%:*}"
    value="${setting#*:}"
    case "$value" in
        ''|*[!0-9]*)
            log "[ERROR] ${name} 必须是非负整数: ${value}"
            exit 2
            ;;
    esac
done
if [ "$EMERGENCY_RETENTION_DAYS" -eq 0 ] || \
   [ "$DISK_HIGH_WATERMARK" -eq 0 ] || [ "$DISK_HIGH_WATERMARK" -gt 100 ] || \
   [ "$DISK_CRITICAL_WATERMARK" -eq 0 ] || [ "$DISK_CRITICAL_WATERMARK" -gt 100 ] || \
   [ "$DISK_HIGH_WATERMARK" -ge "$DISK_CRITICAL_WATERMARK" ]; then
    log "[ERROR] 磁盘水位参数无效: high=${DISK_HIGH_WATERMARK}, critical=${DISK_CRITICAL_WATERMARK}"
    exit 2
fi

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

DISK_SNAPSHOT="$(df -Pk "$ROOT" 2>/dev/null | awk 'NR == 2 {gsub(/%/, "", $5); print $4, $5}')"
FREE_KB="${DISK_SNAPSHOT%% *}"
DISK_USED="${DISK_SNAPSHOT##* }"
if ! [[ "$FREE_KB" =~ ^[0-9]+$ && "$DISK_USED" =~ ^[0-9]+$ ]]; then
    log "[ERROR] 无法读取磁盘水位"
    alert "缓存清理失败" "无法读取磁盘水位，清理未执行。" || true
    exit 1
fi
FREE_MB=$((FREE_KB / 1024))
EFFECTIVE_RETENTION_DAYS="$RETENTION_DAYS"
EMERGENCY_MODE=0
if [ "$DISK_USED" -ge "$DISK_HIGH_WATERMARK" ] || [ "$FREE_MB" -lt "$MIN_FREE_MB" ]; then
    EFFECTIVE_RETENTION_DAYS="$EMERGENCY_RETENTION_DAYS"
    EMERGENCY_MODE=1
fi

log "=== 开始清理过期容器缓存（常规保留 ${RETENTION_DAYS} 天，本次保留 ${EFFECTIVE_RETENTION_DAYS} 天）==="

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
CLIENT_TIMEOUT=$((10#$DOCKER_TIMEOUT + 6))

# 已经落盘且通过质量门禁的 paper 不再需要 Docker workfolder。以 paper store
# 和 failure sidecar 为准回收整篇缓存；这比单纯依赖 mtime 更可靠，也不会
# 删除待修复 paper 的源码现场。全文翻译锁已持有，因此不会与活跃任务竞态。
RECLAIMABLE_IDS=()
if [ -f "$RECLAIM_HELPER" ]; then
    RECLAIM_OUTPUT="$($PYTHON "$RECLAIM_HELPER" --root "$ROOT" --ids 2>&1)"
    RECLAIM_STATUS=$?
    if [ "$RECLAIM_STATUS" -ne 0 ]; then
        log "[WARN] 无法生成已发布 paper 缓存回收清单（status=${RECLAIM_STATUS}）：${RECLAIM_OUTPUT}"
    elif [ -n "$RECLAIM_OUTPUT" ]; then
        while IFS= read -r aid; do
            [ -n "$aid" ] || continue
            RECLAIMABLE_IDS+=("$aid")
        done <<< "$RECLAIM_OUTPUT"
    fi
else
    log "[WARN] 缓存回收策略脚本不存在：${RECLAIM_HELPER}"
fi

RECLAIMED_COUNT=0
if [ "${#RECLAIMABLE_IDS[@]}" -gt 0 ]; then
    RECLAIM_OUTPUT="$(
        timeout --signal=TERM --kill-after=5s "${CLIENT_TIMEOUT}s" \
        docker exec "$CONTAINER" \
        timeout --signal=TERM --kill-after=5s "${DOCKER_TIMEOUT}s" \
        sh -c '
            set -eu
            root=/gpt/gpt_log/arxiv_cache
            for aid in "$@"; do
                case "$aid" in
                    [0-9][0-9][0-9][0-9].[0-9][0-9][0-9][0-9]*) ;;
                    *) continue ;;
                esac
                entry="$root/$aid"
                [ -d "$entry" ] || continue
                printf "RECLAIM %s\\n" "$entry"
                find "$entry" -depth -delete
            done
        ' sh "${RECLAIMABLE_IDS[@]}" 2>&1
    )"
    RECLAIM_STATUS=$?
    if [ "$RECLAIM_STATUS" -ne 0 ]; then
        if [ "$RECLAIM_STATUS" -eq 124 ] || [ "$RECLAIM_STATUS" -eq 137 ]; then
            log "[ERROR] 已发布 paper 缓存回收超时（${DOCKER_TIMEOUT}s）：${RECLAIM_OUTPUT}"
        else
            log "[ERROR] 已发布 paper 缓存回收失败（status=${RECLAIM_STATUS}）：${RECLAIM_OUTPUT}"
        fi
        exit 1
    fi
    if [ -n "$RECLAIM_OUTPUT" ]; then
        printf '%s\n' "$RECLAIM_OUTPUT" >> "$LOG"
    fi
    RECLAIMED_COUNT="$(
        printf '%s\n' "$RECLAIM_OUTPUT" |
            awk '/^RECLAIM / {count += 1} END {print count + 0}'
    )"
fi

# arxiv_cache 以论文目录为删除单元；default_user/admin 则按文件年龄清理。
# 后者不能把 shared/downloadzone 当成一个整体，否则任意近期文件都会让数 GB
# 旧 zip 永久保留。缓存根目录本身永远保留。
CLEAN_OUTPUT="$(
    timeout --signal=TERM --kill-after=5s "${CLIENT_TIMEOUT}s" \
    docker exec "$CONTAINER" \
    timeout --signal=TERM --kill-after=5s "${DOCKER_TIMEOUT}s" \
    sh -c '
        set -eu
        retention_days="$1"
        ephemeral_grace_minutes="$2"
        emergency_mode="$3"
        root=/gpt/gpt_log/arxiv_cache
        if [ -d "$root" ]; then
            find "$root" -mindepth 1 -maxdepth 1 \
                \( -type f -o -type l \) -mtime "+$retention_days" \
                -printf "DELETE %p\n" -delete
            old_dirs="$(find "$root" -mindepth 1 -maxdepth 1 -type d \
                -mtime "+$retention_days" -print)"
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
        fi

        for root in /gpt/gpt_log/default_user /gpt/gpt_log/admin
        do
            [ -d "$root" ] || continue
            if [ "$emergency_mode" -eq 1 ]; then
                # downloadzone/shared are presentation artifacts, not paper
                # state. Once the shared lock is held, an emergency run may
                # reclaim files older than the short grace window.
                find "$root" -mindepth 1 \( -type f -o -type l \) \
                    -mmin "+$ephemeral_grace_minutes" \
                    -printf "DELETE_EPHEMERAL %p\n" -delete
            else
                find "$root" -mindepth 1 \( -type f -o -type l \) \
                    -mtime "+$retention_days" -printf "DELETE %p\n" -delete
            fi
            find "$root" -mindepth 1 -depth -type d -empty \
                -printf "DELETE %p\n" -delete
        done
    ' sh "$EFFECTIVE_RETENTION_DAYS" "$EPHEMERAL_GRACE_MINUTES" "$EMERGENCY_MODE" 2>&1
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
        awk '/^(DELETE|DELETE_EPHEMERAL) / {count += 1} END {print count + 0}'
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
TOTAL_DELETED=$((DELETED_COUNT + RECLAIMED_COUNT))
if [ "$RECLAIMED_COUNT" -gt 0 ]; then
    log "[INFO] 已发布 paper 缓存回收=${RECLAIMED_COUNT}，本轮总删除=${TOTAL_DELETED}"
fi

AFTER_SNAPSHOT="$(df -Pk "$ROOT" 2>/dev/null | awk 'NR == 2 {gsub(/%/, "", $5); print $4, $5}')"
AFTER_FREE_KB="${AFTER_SNAPSHOT%% *}"
AFTER_USED="${AFTER_SNAPSHOT##* }"
if [[ "$AFTER_FREE_KB" =~ ^[0-9]+$ && "$AFTER_USED" =~ ^[0-9]+$ ]]; then
    AFTER_FREE_MB=$((AFTER_FREE_KB / 1024))
else
    AFTER_FREE_MB=0
    AFTER_USED=100
fi

if [ "$AFTER_USED" -ge "$DISK_CRITICAL_WATERMARK" ] || [ "$AFTER_FREE_MB" -lt "$MIN_FREE_MB" ]; then
    alert "磁盘空间仍然不足" \
        "缓存清理后磁盘使用率 ${AFTER_USED}%，可用 ${AFTER_FREE_MB}MB；删除 ${TOTAL_DELETED} 项。需要人工处理。" || true
    log "[ERROR] 清理后磁盘仍处于危险水位"
    exit 1
fi
if [ "$EMERGENCY_MODE" -eq 1 ]; then
    alert "磁盘高水位已自动清理" \
        "清理前磁盘使用率 ${DISK_USED}%、可用 ${FREE_MB}MB；清理后 ${AFTER_USED}%、可用 ${AFTER_FREE_MB}MB；删除 ${TOTAL_DELETED} 项。" || true
fi
log "=== 清理完成 ==="
