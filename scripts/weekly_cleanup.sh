#!/bin/bash
# 每周系统缓存清理脚本
# crontab: 0 4 * * 0  (每周日 04:00，在 Docker 缓存清理 03:30 之后)
#
# 清理范围（均为缓存/临时文件，不触碰有效数据）：
#   - pip HTTP 下载缓存
#   - systemd journal（保留最近 50MB）
#   - 旧 rotated 系统日志（保留最近14天）
#   - nginx 访问/错误日志（超过 50MB 时截断）
#   - dnf 包缓存
#   - /tmp 超过7天的临时文件
#   - paper-trans 孤立 PDF（paper store 中不被任何 index.json 引用的）
#   - 旧 Cursor Server 版本（保留当前版本）
#   - 系统 PageCache（sync 后释放，内核自动重建）

LOG=/root/workspace/paper-trans/logs/cleanup.log
PTDIR=/root/workspace/paper-trans

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

log "========================================"
log "开始每周系统清理"
DISK_BEFORE=$(df / | awk 'NR==2{print $4}')
log "清理前可用空间: $(df -h / | awk 'NR==2{print $4}')"

# ── 1. pip HTTP 下载缓存 ─────────────────────────────────────────────────────
PIP_BEFORE=$(du -sh ~/.cache/pip/ 2>/dev/null | awk '{print $1}')
pip cache purge >/dev/null 2>&1
log "[pip] 清理完成（清理前 ${PIP_BEFORE:-0}）"

# ── 2. systemd journal（保留最近 50MB）──────────────────────────────────────
JOURNAL_FREED=$(journalctl --vacuum-size=50M 2>&1 | grep -oP '[\d.]+ [KMG]iB' | tail -1)
log "[journal] 清理完成，释放 ${JOURNAL_FREED:-0}"

# ── 3. 旧 rotated 系统日志（保留14天内）────────────────────────────────────
CUTOFF=$(date -d '14 days ago' '+%Y%m%d')
REMOVED_LOGS=0
for f in /var/log/messages-* /var/log/kern-* /var/log/secure-* /var/log/btmp-*; do
    [ -f "$f" ] || continue
    # 从文件名提取日期（格式 YYYYMMDD）
    fname_date=$(basename "$f" | grep -oP '\d{8}$')
    [ -z "$fname_date" ] && continue
    if [ "$fname_date" -lt "$CUTOFF" ]; then
        rm -f "$f" && REMOVED_LOGS=$((REMOVED_LOGS + 1))
    fi
done
# 清理旧 dnf 日志（保留最近1个）
rm -f /var/log/dnf.log.2 /var/log/dnf.librepo.log.1 2>/dev/null
log "[rotated logs] 删除 ${REMOVED_LOGS} 个旧日志文件"

# ── 4. nginx 访问/错误日志（超 50MB 才截断）────────────────────────────────
NGINX_TRUNCATED=0
for f in /www/wwwlogs/*.log; do
    [ -f "$f" ] || continue
    size=$(stat -c%s "$f" 2>/dev/null || echo 0)
    if [ "$size" -gt $((50 * 1024 * 1024)) ]; then
        truncate -s 0 "$f"
        NGINX_TRUNCATED=$((NGINX_TRUNCATED + 1))
    fi
done
log "[nginx logs] 截断 ${NGINX_TRUNCATED} 个超大日志文件"

# ── 5. dnf 包缓存 ────────────────────────────────────────────────────────────
dnf clean packages >/dev/null 2>&1
log "[dnf] 包缓存清理完成"

# ── 6. /tmp 超过7天的临时文件 ────────────────────────────────────────────────
TMP_REMOVED=$(find /tmp -maxdepth 1 -type f -mtime +7 -delete -print 2>/dev/null | wc -l)
log "[tmp] 删除 ${TMP_REMOVED} 个7天未使用的临时文件"

# ── 7. paper-trans 孤立 PDF（不被任何 index.json 引用）─────────────────────
ORPHAN_RESULT=$(/root/.pyenv/versions/3.10.13/bin/python3 - << 'PYEOF'
import os, json
DATA_DIR = "/root/workspace/paper-trans/data"
PAPERS_DIR = os.path.join(DATA_DIR, "papers")
used_ids = set()
for mode in ("daily", "weekly", "monthly", "manual"):
    mode_dir = os.path.join(DATA_DIR, mode)
    if not os.path.isdir(mode_dir): continue
    for key in os.listdir(mode_dir):
        idx = os.path.join(mode_dir, key, "index.json")
        if not os.path.exists(idx): continue
        try:
            with open(idx) as f:
                d = json.load(f)
            for p in d.get("papers", []):
                if p.get("arxiv_id"): used_ids.add(p["arxiv_id"])
        except: pass
removed, freed = 0, 0
for fn in os.listdir(PAPERS_DIR):
    if not fn.endswith("_zh.pdf"): continue
    aid = fn.replace("_zh.pdf", "")
    if aid not in used_ids:
        p = os.path.join(PAPERS_DIR, fn)
        freed += os.path.getsize(p)
        os.remove(p)
        removed += 1
print(f"删除 {removed} 个孤立PDF，释放 {freed//1048576} MB")
PYEOF
)
log "[orphan PDF] ${ORPHAN_RESULT}"

# ── 8. 旧 Cursor Server 版本（保留当前进程使用的版本）──────────────────────
CURSOR_BIN="/root/.cursor-server/bin/linux-x64"
CURRENT_VER=$(ps aux | grep "cursor-server.*server-main" | grep -v grep \
    | grep -oP '/linux-x64/\K[a-f0-9]{40}' | head -1)
if [ -n "$CURRENT_VER" ] && [ -d "$CURSOR_BIN/$CURRENT_VER" ]; then
    CURSOR_REMOVED=0
    for ver_dir in "$CURSOR_BIN"/*/; do
        ver=$(basename "$ver_dir")
        if [ "$ver" != "$CURRENT_VER" ]; then
            rm -rf "$ver_dir"
            CURSOR_REMOVED=$((CURSOR_REMOVED + 1))
        fi
    done
    log "[cursor] 删除 ${CURSOR_REMOVED} 个旧版本（保留 ${CURRENT_VER:0:8}...）"
else
    log "[cursor] 未检测到当前版本，跳过"
fi

# ── 9. 系统 PageCache ────────────────────────────────────────────────────────
sync && echo 1 > /proc/sys/vm/drop_caches
log "[pagecache] 已释放"

# ── 完成统计 ─────────────────────────────────────────────────────────────────
DISK_AFTER=$(df / | awk 'NR==2{print $4}')
FREED_KB=$((DISK_AFTER - DISK_BEFORE))
FREED_MB=$((FREED_KB / 1024))
log "清理后可用空间: $(df -h / | awk 'NR==2{print $4}')（本次释放约 ${FREED_MB} MB）"
log "========================================"
