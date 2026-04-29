#!/bin/bash
# 系统启动后设置关键进程 OOM 保护分值，防止 OOM killer 误杀服务进程
# 由 systemd (oom-protection.service) 在 multi-user.target 后执行

LOG=/root/workspace/paper-trans/logs/oom-protection.log
echo "=== $(date '+%Y-%m-%d %H:%M:%S') OOM 保护设置 ===" >> "$LOG"

set_oom() {
    local pids score name
    name=$1; score=$2; shift 2
    pids=$(pgrep -f "$@" 2>/dev/null)
    if [ -z "$pids" ]; then
        echo "[SKIP] 未找到进程: $name" >> "$LOG"
        return
    fi
    for pid in $pids; do
        echo "$score" > "/proc/$pid/oom_score_adj" 2>/dev/null && \
            echo "[OK] $name pid=$pid oom_score_adj=$score" >> "$LOG"
    done
}

# nginx
for pid in $(pgrep nginx 2>/dev/null); do
    echo -200 > /proc/$pid/oom_score_adj 2>/dev/null
done
echo "[OK] nginx oom_score_adj=-200" >> "$LOG"

# paper-trans web server
set_oom "paper-trans web_server" -200 "web_server.py"

# BT Panel
set_oom "BT-Panel" -200 "BT-Panel"
set_oom "BT-Task"  -200 "BT-Task"

echo "[DONE] OOM 保护设置完成" >> "$LOG"
