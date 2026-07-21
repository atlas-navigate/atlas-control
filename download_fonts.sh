#!/bin/bash
# Download Protomaps offline glyph (font) PBF files for MapLibre vector map rendering.
# Run once when you have internet access. Files are ~8MB total and work fully offline after.
#
# About "missing" ranges (why this used to abort the install):
#   The Protomaps CDN only hosts the glyph ranges a given font weight actually
#   contains. Lighter-covered weights — notably "Noto Sans Italic" — legitimately
#   lack many Unicode blocks (CJK, private-use area, various symbol blocks, …),
#   so the CDN returns HTTP 404 for those ranges. That is EXPECTED, not an error:
#   MapLibre just falls back for any codepoint an italic weight doesn't cover.
#
#   So we treat 404 as "range not published for this weight" (skip it) and only
#   fail on genuine network problems (DNS/connection/timeout/5xx). The Regular
#   weight is the primary label font, so we do require it to come through.

FONTS_DIR="$(dirname "$0")/static/fonts"
BASE_URL="https://cdn.protomaps.com/fonts/pbf"

FONTS=(
  "Noto Sans Regular"
  "Noto Sans Italic"
  "Noto Sans Bold"
  "Noto Sans Medium"
)

# The primary weight used for map labels — expected to be essentially complete.
PRIMARY_FONT="Noto Sans Regular"
PRIMARY_MIN=255          # allow one range of slack out of 256

NET_FAIL=0               # set on a real network/server error (retryable)

for FONT in "${FONTS[@]}"; do
  ENCODED=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$FONT'))")
  DIR="$FONTS_DIR/$FONT"
  mkdir -p "$DIR"
  echo "Downloading glyphs for: $FONT"
  DOWNLOADED=0
  MISSING=0
  for START in $(seq 0 256 65280); do
    END=$((START + 255))
    RANGE="${START}-${END}"
    OUT="$DIR/${RANGE}.pbf"
    if [ -f "$OUT" ]; then
      DOWNLOADED=$((DOWNLOADED + 1))
      continue
    fi
    # Capture the HTTP status so we can tell a legitimate 404 apart from a
    # network failure. curl's exit code (RC) catches DNS/connect/timeout errors;
    # HTTP tells us what the server actually said.
    HTTP=$(curl -s --max-time 60 -o "$OUT" -w '%{http_code}' \
      "${BASE_URL}/${ENCODED}/${RANGE}.pbf")
    RC=$?
    if [ "$RC" -ne 0 ]; then
      # Couldn't reach the CDN at all (offline, DNS, connection reset, timeout).
      rm -f "$OUT"
      echo "  ! network error (curl exit $RC): $FONT $RANGE"
      NET_FAIL=1
    elif [ "$HTTP" = "200" ]; then
      DOWNLOADED=$((DOWNLOADED + 1))
    elif [ "$HTTP" = "404" ]; then
      # This weight has no glyphs in this Unicode range — expected, skip quietly.
      rm -f "$OUT"
      MISSING=$((MISSING + 1))
    else
      # 5xx or other unexpected status — treat as a retryable failure.
      rm -f "$OUT"
      echo "  ! HTTP $HTTP: $FONT $RANGE"
      NET_FAIL=1
    fi
  done
  if [ "$MISSING" -gt 0 ]; then
    echo "  → $DOWNLOADED glyph ranges downloaded (${MISSING} range(s) not published for this weight — normal)"
  else
    echo "  → $DOWNLOADED glyph ranges downloaded"
  fi
done

# A genuine network/server error is the only fatal condition — those are worth
# retrying. Missing ranges (404s) are not.
if [ "$NET_FAIL" -ne 0 ]; then
  echo "Font download hit network/server errors — re-run when connectivity is stable."
  exit 1
fi

# Make sure the primary label weight actually came through.
PRIMARY_COUNT=$(ls "$FONTS_DIR/$PRIMARY_FONT"/*.pbf 2>/dev/null | wc -l)
if [ "$PRIMARY_COUNT" -lt "$PRIMARY_MIN" ]; then
  echo "Primary font '$PRIMARY_FONT' incomplete ($PRIMARY_COUNT/$PRIMARY_MIN ranges) — re-run when online."
  exit 1
fi

echo "Done. Restart atlas-control to serve fonts offline."
