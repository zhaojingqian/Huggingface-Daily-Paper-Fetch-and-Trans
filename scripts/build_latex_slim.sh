#!/usr/bin/env bash
# Build a smaller gpt-academic LaTeX translation image from the current runtime.
#
# Default path is intentionally disk-frugal for the 40G production server:
#   1. create a temporary container from the current production image
#   2. copy the currently patched /gpt code from the running production container
#   3. remove heavyweight ML/runtime cache payloads
#   4. keep the source image's full TeX runtime by default, then docker export |
#      docker import to flatten the result into a smaller image
#
# The production container and image are not replaced or deleted.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_CONTAINER="${GPT_ACADEMIC_SOURCE_CONTAINER:-${GPT_ACADEMIC_CONTAINER:-gpt-academic-latex}}"
IMAGE="${GPT_ACADEMIC_SLIM_IMAGE:-paper-trans-latex-slim:latest}"
TMP_CONTAINER="${GPT_ACADEMIC_SLIM_BUILD_CONTAINER:-paper-trans-latex-slim-build-$$}"
SOURCE_IMAGE="${GPT_ACADEMIC_SOURCE_IMAGE:-}"
MODE="${GPT_ACADEMIC_SLIM_BUILD_MODE:-flatten}"
TEX_PROFILE="${GPT_ACADEMIC_SLIM_TEX_PROFILE:-full}"
EXPORT_ARCHIVE="${GPT_ACADEMIC_SLIM_EXPORT_ARCHIVE:-}"
EXPORT_COMPRESSOR="${GPT_ACADEMIC_SLIM_EXPORT_COMPRESSOR:-pigz}"

cleanup() {
  docker rm -f "$TMP_CONTAINER" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "[slim-build] source container: ${SOURCE_CONTAINER}"
echo "[slim-build] target image: ${IMAGE}"
echo "[slim-build] mode: ${MODE}"
echo "[slim-build] tex profile: ${TEX_PROFILE}"
if [ -n "$EXPORT_ARCHIVE" ]; then
  echo "[slim-build] export archive: ${EXPORT_ARCHIVE}"
  echo "[slim-build] export compressor: ${EXPORT_COMPRESSOR}"
fi

if ! docker ps --format '{{.Names}}' | grep -q "^${SOURCE_CONTAINER}$"; then
  echo "[slim-build] ERROR: source container is not running: ${SOURCE_CONTAINER}" >&2
  exit 1
fi

if [ "$TEX_PROFILE" != "full" ] && [ "$TEX_PROFILE" != "slim" ]; then
  echo "[slim-build] ERROR: GPT_ACADEMIC_SLIM_TEX_PROFILE must be full or slim" >&2
  exit 1
fi

if [ "$MODE" = "dockerfile" ]; then
  CONTEXT="$(mktemp -d /tmp/paper-trans-latex-slim.XXXXXX)"
  trap 'rm -rf "$CONTEXT"; cleanup' EXIT
  docker cp "${SOURCE_CONTAINER}:/gpt" "${CONTEXT}/gpt"
  rm -rf \
    "${CONTEXT}/gpt/gpt_log" \
    "${CONTEXT}/gpt/.git" \
    "${CONTEXT}/gpt/config_private.py" \
    "${CONTEXT}/gpt/__pycache__"
  find "${CONTEXT}/gpt" -type d -name '__pycache__' -prune -exec rm -rf {} +
  find "${CONTEXT}/gpt" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete
  docker build -f "${ROOT_DIR}/docker/latex-slim/Dockerfile" -t "$IMAGE" "$CONTEXT"
  docker images "$IMAGE"
  exit 0
fi

if [ -z "$SOURCE_IMAGE" ]; then
  SOURCE_IMAGE="$(docker inspect -f '{{.Config.Image}}' "$SOURCE_CONTAINER")"
fi

echo "[slim-build] source image: ${SOURCE_IMAGE}"
echo "[slim-build] temporary container: ${TMP_CONTAINER}"

docker create --name "$TMP_CONTAINER" --network=host "$SOURCE_IMAGE" bash -lc 'tail -f /dev/null' >/dev/null
docker start "$TMP_CONTAINER" >/dev/null

echo "[slim-build] copying patched /gpt code without runtime cache"
docker exec -u root -w / "$TMP_CONTAINER" rm -rf /gpt
docker exec "$SOURCE_CONTAINER" tar \
  --exclude='./gpt_log' \
  --exclude='./config_private.py' \
  --exclude='./.git' \
  --exclude='./__pycache__' \
  --exclude='*.pyc' \
  --exclude='*.pyo' \
  -C /gpt -cf - . \
  | docker exec -i -u root -w / "$TMP_CONTAINER" bash -lc 'mkdir -p /gpt && tar -C /gpt -xf -'

echo "[slim-build] pruning heavy runtime payloads in temporary container"
docker exec -u root -e TEX_PROFILE="$TEX_PROFILE" "$TMP_CONTAINER" bash -lc '
set -euo pipefail
rm -rf /gpt/gpt_log /gpt/config_private.py /tmp/* /root/.cache /home/gptuser/.cache
mkdir -p /gpt/gpt_log/arxiv_cache
chown -R gptuser:gptuser /gpt /home/gptuser

PY_SITE=/usr/local/lib/python3.12/dist-packages
rm -rf \
  "$PY_SITE"/nvidia \
  "$PY_SITE"/nvidia-* \
  "$PY_SITE"/torch \
  "$PY_SITE"/torch-* \
  "$PY_SITE"/torchvision \
  "$PY_SITE"/torchvision-* \
  "$PY_SITE"/triton \
  "$PY_SITE"/triton-* \
  "$PY_SITE"/cuda \
  "$PY_SITE"/*cuda*.pth \
  "$PY_SITE"/_cuda_bindings_redirector.py \
  "$PY_SITE"/albumentations* \
  "$PY_SITE"/blis* \
  "$PY_SITE"/cv2 \
  "$PY_SITE"/datasets* \
  "$PY_SITE"/hf_xet* \
  "$PY_SITE"/lightning* \
  "$PY_SITE"/llama_index* \
  "$PY_SITE"/nougat* \
  "$PY_SITE"/opencv_python_headless* \
  "$PY_SITE"/pyarrow* \
  "$PY_SITE"/pytorch_lightning* \
  "$PY_SITE"/safetensors* \
  "$PY_SITE"/scipy* \
  "$PY_SITE"/sentencepiece* \
  "$PY_SITE"/spacy* \
  "$PY_SITE"/sympy* \
  "$PY_SITE"/thinc* \
  "$PY_SITE"/timm* \
  "$PY_SITE"/tokenizers* \
  "$PY_SITE"/torchgen \
  "$PY_SITE"/torchmetrics* \
  "$PY_SITE"/transformers* \
  "$PY_SITE"/xformers*

if [ "$TEX_PROFILE" = "slim" ]; then
  apt-get purge -y \
    asymptote asymptote-doc \
    context context-modules \
    default-jre default-jre-headless \
    openjdk-21-jre openjdk-21-jre-headless \
    texlive-fonts-extra texlive-fonts-extra-doc texlive-full \
    texlive-fonts-recommended-doc \
    texlive-games texlive-humanities texlive-humanities-doc texlive-music \
    texlive-lang-arabic texlive-lang-cyrillic texlive-lang-czechslovak \
    texlive-lang-french texlive-lang-german \
    texlive-lang-greek texlive-lang-italian texlive-lang-japanese \
    texlive-lang-korean texlive-lang-polish texlive-lang-portuguese \
    texlive-lang-spanish \
    texlive-latex-base-doc texlive-latex-extra-doc texlive-latex-recommended-doc \
    texlive-metapost-doc texlive-pictures-doc texlive-pstricks-doc \
    texlive-publishers-doc texlive-science-doc \
    >/dev/null 2>&1 || true
else
  echo "[slim-build] keeping full TeX/font runtime from source image"
fi
apt-get clean
rm -rf \
  /var/lib/apt/lists/* \
  /var/cache/apt/* \
  /usr/share/doc \
  /usr/share/info \
  /usr/share/man \
  /usr/share/texlive/texmf-dist/doc \
  /usr/share/texlive/texmf-dist/source \
  /usr/share/texmf/doc
if [ "$TEX_PROFILE" = "slim" ]; then
  rm -rf \
    /usr/share/fonts/custom \
    /usr/share/texlive/texmf-dist/tex4ht
fi
find / -xdev -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true
find / -xdev -type f \( -name "*.pyc" -o -name "*.pyo" \) -delete 2>/dev/null || true
'

echo "[slim-build] applying paper-trans LaTeX patches to temporary container"
GPT_ACADEMIC_CONTAINER="$TMP_CONTAINER" bash "${ROOT_DIR}/scripts/setup_docker_env.sh"

echo "[slim-build] estimated flattened rootfs size:"
docker exec -u root "$TMP_CONTAINER" du -xsh /

if [ "${GPT_ACADEMIC_SLIM_DRY_RUN:-0}" = "1" ]; then
  echo "[slim-build] dry run requested; skipping docker import"
  exit 0
fi

if [ -n "$EXPORT_ARCHIVE" ]; then
  echo "[slim-build] exporting flattened rootfs archive"
  mkdir -p "$(dirname "$EXPORT_ARCHIVE")"
  case "$EXPORT_COMPRESSOR" in
    pigz)
      docker export "$TMP_CONTAINER" | pigz -6 > "$EXPORT_ARCHIVE"
      ;;
    gzip)
      docker export "$TMP_CONTAINER" | gzip -6 > "$EXPORT_ARCHIVE"
      ;;
    xz)
      docker export "$TMP_CONTAINER" | xz -T0 -3 > "$EXPORT_ARCHIVE"
      ;;
    none)
      docker export "$TMP_CONTAINER" > "$EXPORT_ARCHIVE"
      ;;
    *)
      echo "[slim-build] ERROR: unsupported compressor: ${EXPORT_COMPRESSOR}" >&2
      exit 1
      ;;
  esac
  ls -lh "$EXPORT_ARCHIVE"
  exit 0
fi

echo "[slim-build] importing flattened image"
docker export "$TMP_CONTAINER" | docker import \
  -c 'WORKDIR /gpt' \
  -c 'USER gptuser' \
  -c 'CMD ["bash","-lc","tail -f /dev/null"]' \
  - "$IMAGE" >/dev/null

echo "[slim-build] built image:"
docker images "$IMAGE"
