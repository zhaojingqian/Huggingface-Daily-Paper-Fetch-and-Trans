#!/usr/bin/env bash
# 实时监控容器内全文翻译进度
# 用法: ./scripts/watch_translate.sh [arxiv_id]
# 不传参数则自动检测正在翻译的论文

CONTAINER="gpt-academic-latex"
AID="${1:-}"

# 自动检测
if [ -z "$AID" ]; then
  AID=$(docker exec "$CONTAINER" sh -c "ps aux | grep full_translate_driver | grep -v grep | grep -oE '[0-9]{4}\.[0-9]+'" 2>/dev/null | head -1)
fi

if [ -z "$AID" ]; then
  echo "未检测到正在翻译的论文，请传入 arxiv_id"
  exit 1
fi

echo "🔍 监控论文: $AID"
CACHE="/gpt/gpt_log/arxiv_cache/$AID"

while true; do
  clear
  TS=$(date '+%H:%M:%S')

  # 进程还在吗
  PROC=$(docker exec "$CONTAINER" sh -c "ps aux | grep full_translate_driver | grep $AID | grep -v grep" 2>/dev/null)
  HOST_PROC=$(ps aux | grep "full_translate_driver.py $AID" | grep -v grep | head -1)

  echo "[$TS] 翻译监控: $AID"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

  # 阶段 1: 下载
  EPRINT=$(docker exec "$CONTAINER" sh -c "ls -lh ${CACHE}/e-print/*.tar 2>/dev/null | tail -1")
  if [ -n "$EPRINT" ]; then
    echo "✅ [1/4] 下载 LaTeX 源码: $EPRINT"
  else
    echo "⏳ [1/4] 下载 LaTeX 源码..."
  fi

  # 阶段 2: 解压预处理
  TEX=$(docker exec "$CONTAINER" sh -c "ls ${CACHE}/extract/*.tex 2>/dev/null | wc -l")
  if [ "${TEX:-0}" -gt 0 ]; then
    echo "✅ [2/4] 解压/预处理: ${TEX} 个 .tex 文件"
  else
    echo "⏳ [2/4] 解压/预处理..."
  fi

  # 阶段 3: 翻译 + 编译（最关键，细化）
  echo ""
  echo "📋 [3/4] 翻译 + LaTeX 编译阶段:"
  TRANS_HTML=$(docker exec "$CONTAINER" sh -c "ls -lht ${CACHE}/workfolder/*.trans.html 2>/dev/null | head -1")
  if [ -n "$TRANS_HTML" ]; then
    echo "   ✅ 中文翻译 HTML 已生成: $TRANS_HTML"
  else
    echo "   ⏳ 中文翻译 HTML 生成中..."
  fi

  # 编译轮次
  FIX_COUNT=$(docker exec "$CONTAINER" sh -c "ls ${CACHE}/workfolder/merge_translate_zh_fix_*.pdf 2>/dev/null | wc -l")
  MERGE_PDF=$(docker exec "$CONTAINER" sh -c "ls -lh ${CACHE}/workfolder/merge.pdf 2>/dev/null | tail -1")
  if [ -n "$MERGE_PDF" ]; then
    echo "   ✅ merge.pdf 已生成: $MERGE_PDF"
  fi
  if [ "${FIX_COUNT:-0}" -gt 0 ]; then
    LATEST_FIX=$(docker exec "$CONTAINER" sh -c "ls -t ${CACHE}/workfolder/merge_translate_zh_fix_*.pdf 2>/dev/null | head -1")
    FIX_SIZE=$(docker exec "$CONTAINER" sh -c "du -sh $LATEST_FIX 2>/dev/null | cut -f1")
    echo "   🔄 LaTeX 编译: fix_${FIX_COUNT} 轮，最新 PDF=${FIX_SIZE}"
    # 末尾 log
    LOG_TAIL=$(docker exec "$CONTAINER" sh -c "tail -2 ${CACHE}/workfolder/merge.log 2>/dev/null")
    echo "   📝 编译 log: $LOG_TAIL"
  else
    echo "   ⏳ LaTeX 编译中..."
    LOG_TAIL=$(docker exec "$CONTAINER" sh -c "tail -2 ${CACHE}/workfolder/merge.log 2>/dev/null")
    [ -n "$LOG_TAIL" ] && echo "   📝 编译 log: $LOG_TAIL"
  fi

  # 阶段 4: 最终 PDF
  echo ""
  FINAL_PDF=$(docker exec "$CONTAINER" sh -c "ls -lh ${CACHE}/translation/translate_zh.pdf 2>/dev/null")
  if [ -n "$FINAL_PDF" ]; then
    echo "✅ [4/4] 最终 PDF 已生成: $FINAL_PDF"
    echo ""
    echo "🎉 翻译完成！等待宿主机复制..."
  else
    echo "⏳ [4/4] 等待最终 PDF 输出..."
  fi

  echo ""
  if [ -n "$HOST_PROC" ]; then
    echo "🖥  宿主机进程: 运行中 ✅"
  else
    echo "🖥  宿主机进程: 已结束"
    # 检查宿主机是否已复制 PDF
    HOST_PDF=$(find /root/workspace/paper-trans/data -name "${AID}_zh.pdf" 2>/dev/null | head -1)
    [ -n "$HOST_PDF" ] && echo "   📄 本地 PDF: $HOST_PDF ($(du -sh $HOST_PDF | cut -f1))"
  fi

  echo ""
  echo "每 15s 刷新 | Ctrl+C 退出"
  sleep 15
done
