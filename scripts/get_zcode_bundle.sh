#!/bin/bash
# Builds runs/zcode-0.16.9/, the files p1agents.ZCode uploads into each task container.
# runs/ is git-ignored, so every teammate builds it once on their own Mac:
#   - zcode.cjs + zcode-builtin.json come from vendor/zcode-0.16.9/ in this repo;
#   - the Node 24 tarballs come from nodejs.org (zcode.cjs needs node:sqlite, Node >= 22.13).
# Every file is checked against the SHA-256 used for Ibrahim's runs, so all columns run the same bytes.
set -euo pipefail
cd "$(dirname "$0")/.."
APP=${ZCODE_APP:-/Applications/ZCode.app}
OUT=runs/zcode-0.16.9
NODE=v24.11.0
mkdir -p "$OUT"

# The two ZCode 0.16.9 files are in vendor/ (private repo); the app is only a fallback, since newer apps ship a different CLI.
for f in zcode.cjs zcode-builtin.json; do
  [ -f "$OUT/$f" ] || cp "vendor/zcode-0.16.9/$f" "$OUT/$f" 2>/dev/null || true
done
[ -f "$OUT/zcode.cjs" ] || cp "$APP/Contents/Resources/glm/zcode.cjs" "$OUT/zcode.cjs"
[ -f "$OUT/zcode-builtin.json" ] || cp "$APP/Contents/Resources/config/provider/zcode-builtin.json" "$OUT/zcode-builtin.json"
for arch in arm64 x64; do
  f="node-$NODE-linux-$arch.tar.xz"
  [ -f "$OUT/$f" ] || curl -fL --retry 3 -o "$OUT/$f" "https://nodejs.org/dist/$NODE/$f"
done

cd "$OUT"
shasum -a 256 -c - <<'EOF'
500ae84fa2cb8dd74c1264e5f3d3709c004c59b58899f1b0c3e205137a0ef466  zcode.cjs
8bd8070d529732900d6bc067a4aa27fef5e06c50b00a32321aa4c3ae6e6b9d20  zcode-builtin.json
33a6673b2c7bffeae9deec7f9f8b31aad9119b08f13d49b2ca3ee3bebfe8260f  node-v24.11.0-linux-arm64.tar.xz
46da9a098973ab7ba4fca76945581ecb2eaf468de347173897044382f10e0a0a  node-v24.11.0-linux-x64.tar.xz
EOF
echo "OK: $OUT matches Ibrahim's ZCode 0.16.9 bundle"
