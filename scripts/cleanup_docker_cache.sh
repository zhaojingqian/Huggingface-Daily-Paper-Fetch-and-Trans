#!/bin/bash
# 定时清理 gpt-academic-latex 容器内的翻译缓存
# 只清容器内的临时缓存，不触碰 /root/workspace/paper-trans/data/（网站数据）
# 建议每月1日 03:00 执行（见 crontab）

LOG=/root/workspace/paper-trans/logs/cleanup.log
CONTAINER=gpt-academic-latex

echo "=== $(date '+%Y-%m-%d %H:%M:%S') 开始清理 ===" >> "$LOG"

# 检查容器是否在运行
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
    echo "[SKIP] 容器 ${CONTAINER} 未运行，跳过清理" >> "$LOG"
    exit 0
fi

# 清理前统计
BEFORE=$(docker exec "$CONTAINER" du -sh /gpt/gpt_log/ 2>/dev/null | awk '{print $1}')
DISK_BEFORE=$(df / | awk 'NR==2{print $5}')
echo "[INFO] 清理前：gpt_log=${BEFORE}，磁盘使用=${DISK_BEFORE}" >> "$LOG"

# 清理 arxiv 下载缓存（翻译时下载的原始 tex/pdf，可重新下载）
docker exec "$CONTAINER" rm -rf /gpt/gpt_log/arxiv_cache/ >> "$LOG" 2>&1
# 清理翻译会话日志（不影响已生成到宿主机的 PDF 文件）
docker exec "$CONTAINER" rm -rf /gpt/gpt_log/default_user/ >> "$LOG" 2>&1
docker exec "$CONTAINER" rm -rf /gpt/gpt_log/admin/ >> "$LOG" 2>&1

# 清理后统计
AFTER=$(docker exec "$CONTAINER" du -sh /gpt/gpt_log/ 2>/dev/null | awk '{print $1}')
DISK_AFTER=$(df / | awk 'NR==2{print $5}')
echo "[INFO] 清理后：gpt_log=${AFTER}，磁盘使用=${DISK_AFTER}" >> "$LOG"
echo "=== 清理完成 ===" >> "$LOG"
