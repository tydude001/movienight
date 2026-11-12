#!/usr/bin/env bash
# Rebuild every icon in app/static/icons from favicon.svg, which is the single
# source — change the artwork there, run this, commit the lot.
#
# Two flags matter and neither is cosmetic. `-alpha remove -alpha off` strips
# transparency because iOS fills an apple-touch-icon's alpha with black; the
# icons ship square and opaque and iOS applies its own squircle mask. And the
# 512px master is rendered at high density *before* being resized down, so the
# small sizes come off a clean raster instead of a 32px one.
#
# Needs ImageMagick 7 (`magick`) with the librsvg delegate:
#   magick -list format | grep RSVG
set -euo pipefail

cd "$(dirname "$0")/.."
icons=app/static/icons
src=$icons/favicon.svg
bg='#171a21'   # the SVG's own ground; what alpha removal composites onto

[ -f "$src" ] || { echo "missing $src" >&2; exit 1; }
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT

magick -background "$bg" -density 384 "$src" -resize 512x512 \
       -alpha remove -alpha off "$tmp/master.png"

for size in 512 192 180; do
  case $size in
    180) out=$icons/apple-touch-icon.png ;;
      *) out=$icons/icon-$size.png ;;
  esac
  magick "$tmp/master.png" -resize "${size}x${size}" -depth 8 -strip "PNG8:$out"
done

for size in 48 32 16; do
  magick "$tmp/master.png" -resize "${size}x${size}" "$tmp/ico-$size.png"
done
magick "$tmp/ico-16.png" "$tmp/ico-32.png" "$tmp/ico-48.png" "$icons/favicon.ico"

echo "rebuilt from $src:"
ls -1 "$icons"
