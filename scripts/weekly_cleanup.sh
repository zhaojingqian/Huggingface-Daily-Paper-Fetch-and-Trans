#!/usr/bin/env bash
# 每周系统缓存清理脚本
# crontab: 0 8 * * 0  (每周日 08:00)
#
# 每个维护步骤都显式检查退出状态；任一步失败会写入同一日志，并在所有安全
# 步骤尝试结束后以非零退出，便于 cron wrapper/监控捕获真实失败。

set -u
set -o pipefail

PTDIR="${PAPER_TRANS_ROOT:-/root/workspace/paper-trans}"
LOG="${PAPER_TRANS_WEEKLY_CLEANUP_LOG:-${PTDIR}/logs/cleanup.log}"
PYTHON="${PAPER_TRANS_PYTHON:-/root/.pyenv/versions/3.10.13/bin/python3}"
DROP_CACHES_FILE="${PAPER_TRANS_DROP_CACHES_FILE:-/proc/sys/vm/drop_caches}"
ERRORS=0

mkdir -p "$(dirname "$LOG")"

# 只写 LOG。生产 cron 已把 stdout 追加到同一文件，若这里再同时复制到 stdout
# 会让每行出现两次；内部日志单写可同时兼容 cron 和手动运行。
log() {
    printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG"
}

fail() {
    ERRORS=$((ERRORS + 1))
    log "[ERROR] $*"
}

log "========================================"
log "开始每周系统清理"

DISK_BEFORE="$(df -P / 2>/dev/null | awk 'NR == 2 {print $4}')"
if [[ "$DISK_BEFORE" =~ ^[0-9]+$ ]]; then
    DISK_BEFORE_OK=1
else
    DISK_BEFORE=0
    DISK_BEFORE_OK=0
    fail "无法读取清理前磁盘空间"
fi
DISK_BEFORE_HUMAN="$(df -hP / 2>/dev/null | awk 'NR == 2 {print $4}')"
log "清理前可用空间: ${DISK_BEFORE_HUMAN:-unknown}"

# ── 1. pip HTTP 下载缓存 ─────────────────────────────────────────────────────
PIP_BEFORE="$(du -sh /root/.cache/pip/ 2>/dev/null | awk 'NR == 1 {print $1}')"
if "$PYTHON" -m pip cache purge >/dev/null 2>&1; then
    log "[pip] 清理完成（清理前 ${PIP_BEFORE:-0}）"
else
    fail "pip 缓存清理失败（解释器 ${PYTHON}）"
fi

# ── 2. systemd journal（保留最近 50MB）──────────────────────────────────────
if JOURNAL_OUTPUT="$(journalctl --vacuum-size=50M 2>&1)"; then
    JOURNAL_FREED="$(
        printf '%s\n' "$JOURNAL_OUTPUT" |
            grep -oP '[\d.]+ [KMG]iB' |
            tail -1 || true
    )"
    log "[journal] 清理完成，释放 ${JOURNAL_FREED:-0}"
else
    fail "journal 清理失败: ${JOURNAL_OUTPUT}"
fi

# ── 3. 旧 rotated 系统日志（保留14天内）─────────────────────────────────────
CUTOFF="$(date -d '14 days ago' '+%Y%m%d')"
REMOVED_LOGS=0
for file in /var/log/messages-* /var/log/kern-* /var/log/secure-* /var/log/btmp-*; do
    [ -f "$file" ] || continue
    filename_date="$(basename "$file" | grep -oP '\d{8}$' || true)"
    [ -n "$filename_date" ] || continue
    if [ "$filename_date" -lt "$CUTOFF" ]; then
        if rm -f "$file"; then
            REMOVED_LOGS=$((REMOVED_LOGS + 1))
        else
            fail "无法删除旧日志 ${file}"
        fi
    fi
done
if ! rm -f /var/log/dnf.log.2 /var/log/dnf.librepo.log.1 2>/dev/null; then
    fail "无法删除旧 dnf 日志"
fi
log "[rotated logs] 删除 ${REMOVED_LOGS} 个旧日志文件"

# ── 4. nginx 访问/错误日志（超 50MB 才截断）────────────────────────────────
NGINX_TRUNCATED=0
for file in /www/wwwlogs/*.log; do
    [ -f "$file" ] || continue
    size="$(stat -c%s "$file" 2>/dev/null || printf '0')"
    if [ "$size" -gt $((50 * 1024 * 1024)) ]; then
        if truncate -s 0 "$file"; then
            NGINX_TRUNCATED=$((NGINX_TRUNCATED + 1))
        else
            fail "无法截断 nginx 日志 ${file}"
        fi
    fi
done
log "[nginx logs] 截断 ${NGINX_TRUNCATED} 个超大日志文件"

# ── 5. dnf 包缓存 ────────────────────────────────────────────────────────────
if dnf clean packages >/dev/null 2>&1; then
    log "[dnf] 包缓存清理完成"
else
    fail "dnf 包缓存清理失败"
fi

# ── 6. /tmp 超过7天的临时文件 ───────────────────────────────────────────────
if TMP_OUTPUT="$(find /tmp -maxdepth 1 -type f -mtime +7 -print -delete 2>&1)"; then
    TMP_REMOVED="$(
        printf '%s\n' "$TMP_OUTPUT" |
            awk 'NF {count += 1} END {print count + 0}'
    )"
    log "[tmp] 删除 ${TMP_REMOVED} 个7天未使用的临时文件"
else
    fail "/tmp 旧文件清理失败: ${TMP_OUTPUT}"
fi

# ── 7. paper-trans 孤立发布对象（PDF / failure sidecar / failed TeX）────────
ORPHAN_GRACE_DAYS="${PAPER_TRANS_ORPHAN_GRACE_DAYS:-3}"
ORPHAN_HELPER="${PTDIR}/scripts/cleanup_orphan_artifacts.py"
if ! [[ "$ORPHAN_GRACE_DAYS" =~ ^[0-9]+$ ]]; then
    fail "孤立对象 grace days 必须是非负整数: ${ORPHAN_GRACE_DAYS}"
    ORPHAN_STATUS=2
    ORPHAN_RESULT="SKIP: 无效 grace days"
elif ORPHAN_RESULT="$(
    "$PYTHON" "$ORPHAN_HELPER" \
        --root "$PTDIR" \
        --grace-days "$ORPHAN_GRACE_DAYS" \
        --apply 2>&1
)"; then
    ORPHAN_STATUS=0
    log "[orphan artifacts] ${ORPHAN_RESULT}"
else
    ORPHAN_STATUS=$?
    fail "孤立对象清理失败（exit=${ORPHAN_STATUS}）: ${ORPHAN_RESULT}"
fi

# ── 8. 旧 Cursor Server 版本（保留当前进程使用的版本）──────────────────────
CURSOR_BIN="/root/.cursor-server/bin/linux-x64"
CURRENT_VER="$(
    ps aux |
        grep "cursor-server.*server-main" |
        grep -v grep |
        grep -oP '/linux-x64/\K[a-f0-9]{40}' |
        head -1 || true
)"
if [ -n "$CURRENT_VER" ] && [ -d "$CURSOR_BIN/$CURRENT_VER" ]; then
    CURSOR_REMOVED=0
    for version_dir in "$CURSOR_BIN"/*/; do
        [ -d "$version_dir" ] || continue
        version="$(basename "$version_dir")"
        if [ "$version" != "$CURRENT_VER" ]; then
            if rm -rf "$version_dir"; then
                CURSOR_REMOVED=$((CURSOR_REMOVED + 1))
            else
                fail "无法删除旧 Cursor Server ${version_dir}"
            fi
        fi
    done
    log "[cursor] 删除 ${CURSOR_REMOVED} 个旧版本（保留 ${CURRENT_VER:0:8}...）"
else
    log "[cursor] 未检测到当前版本，跳过"
fi

# ── 9. 系统 PageCache ────────────────────────────────────────────────────────
if sync && printf '1\n' > "$DROP_CACHES_FILE"; then
    log "[pagecache] 已释放"
else
    fail "PageCache 释放失败（${DROP_CACHES_FILE}）"
fi

# ── 完成统计 ─────────────────────────────────────────────────────────────────
DISK_AFTER="$(df -P / 2>/dev/null | awk 'NR == 2 {print $4}')"
if [[ "$DISK_AFTER" =~ ^[0-9]+$ ]] && [ "$DISK_BEFORE_OK" -eq 1 ]; then
    FREED_KB=$((DISK_AFTER - DISK_BEFORE))
    FREED_MB=$((FREED_KB / 1024))
else
    DISK_AFTER=0
    FREED_MB=0
    fail "无法读取清理后磁盘空间"
fi
DISK_AFTER_HUMAN="$(df -hP / 2>/dev/null | awk 'NR == 2 {print $4}')"
log "清理后可用空间: ${DISK_AFTER_HUMAN:-unknown}（本次释放约 ${FREED_MB} MB）"

if [ "$ERRORS" -gt 0 ]; then
    log "清理结束：${ERRORS} 个步骤失败"
    log "========================================"
    exit 1
fi

log "清理完成：全部步骤成功"
log "========================================"
