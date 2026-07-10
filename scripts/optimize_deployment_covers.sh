#!/bin/sh
set -eu

cover_dir="${1:-dist/client/assets/covers}"
test -d "$cover_dir" || exit 0

if command -v sips >/dev/null 2>&1; then
  find "$cover_dir" -type f \( -name '*.jpg' -o -name '*.jpeg' -o -name '*.png' \) -exec sips -Z 500 {} + >/dev/null
fi

if command -v cwebp >/dev/null 2>&1 && command -v sips >/dev/null 2>&1; then
  find "$cover_dir" -type f -name '*.webp' -print0 | while IFS= read -r -d '' file; do
    height="$(sips -g pixelHeight "$file" | awk '/pixelHeight:/ {print $2}')"
    width="$(sips -g pixelWidth "$file" | awk '/pixelWidth:/ {print $2}')"
    if test "${height:-0}" -gt 500 || test "${width:-0}" -gt 500; then
      tmp="${file}.optimized.webp"
      cwebp -quiet -q 82 -resize 0 500 "$file" -o "$tmp"
      mv "$tmp" "$file"
    fi
  done
fi
