#!/bin/sh
# Downloads the Archivo variable font used by the "ticket" look into
# static/fonts/, so the app never needs Google's servers at runtime.
# Run once from the project root:  sh utils/fetch_fonts.sh
# Without it the ticket look falls back to condensed fonts already on the
# device (Avenir Next Condensed on iPhone/Mac, Roboto Condensed on Android).
set -e
cd "$(dirname "$0")/.."
mkdir -p static/fonts
curl -fL -o static/fonts/Archivo-Variable.ttf \
  "https://github.com/google/fonts/raw/main/ofl/archivo/Archivo%5Bwdth,wght%5D.ttf"
echo "Saved static/fonts/Archivo-Variable.ttf (SIL Open Font License)."
