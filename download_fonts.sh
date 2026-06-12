#!/bin/bash
# Download Protomaps offline glyph (font) PBF files for MapLibre vector map rendering.
# Run once when you have internet access. Files are ~8MB total and work fully offline after.

FONTS_DIR="$(dirname "$0")/static/fonts"
BASE_URL="https://cdn.protomaps.com/fonts/pbf"

FONTS=(
  "Noto Sans Regular"
  "Noto Sans Italic"
  "Noto Sans Bold"
  "Noto Sans Medium"
)

EXPECTED_COUNT=256
FAILED=0

for FONT in "${FONTS[@]}"; do
  ENCODED=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$FONT'))")
  DIR="$FONTS_DIR/$FONT"
  mkdir -p "$DIR"
  echo "Downloading glyphs for: $FONT"
  for START in $(seq 0 256 65280); do
    END=$((START + 255))
    RANGE="${START}-${END}"
    OUT="$DIR/${RANGE}.pbf"
    if [ ! -f "$OUT" ]; then
      if ! curl -sf "${BASE_URL}/${ENCODED}/${RANGE}.pbf" -o "$OUT"; then
        rm -f "$OUT"
        echo "  ! failed: $FONT $RANGE"
        FAILED=1
      fi
    fi
  done
  COUNT=$(ls "$DIR"/*.pbf 2>/dev/null | wc -l)
  echo "  → $COUNT glyph ranges downloaded"
  if [ "$COUNT" -lt "$EXPECTED_COUNT" ]; then
    echo "  ! incomplete font set for: $FONT ($COUNT/$EXPECTED_COUNT)"
    FAILED=1
  fi
done

if [ "$FAILED" -ne 0 ]; then
  echo "Font download incomplete."
  exit 1
fi

echo "Done. Restart atlas-control to serve fonts offline."
