#!/bin/bash
# Build Waudio.app (default ~/Applications/Waudio.app) from Host.swift + Info.plist + the icon,
# with the helpers (Contents/Helpers/Waudio Engine.app, the backend; Waudio Models.app, a model worker)
# and Waudio for Safari (the browser/ extension, Contents/PlugIns/Waudio for Safari.appex) inside.
# Re-run when Host.swift, the icon or the version changes, or the project / Python moves. Python and
# frontend edits need no rebuild (menu bar → Restart). The build is deterministic, so rebuilding
# the same inputs keeps the signature, and macOS keeps the permissions.
#   PYTHON=/path/to/python OUT=/path/Waudio.app packaging/macos/build_app.sh
#   packaging/macos/build_app.sh --entitlements    # only print what the app and engine are signed with
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
PROJECT=$(cd "$HERE/../.." && pwd)
PYTHON=${PYTHON:-$(command -v python3)}
IDENTITY="$PROJECT/backend/waudio/__init__.py"  # the app's name and bundle id: there only
value() { sed -n "s/^$1 = \"\([^\"]*\)\".*/\1/p" "$IDENTITY"; }  # the quoted value, not a comment after it
NAME=$(value NAME); BUNDLE_ID=$(value BUNDLE_ID); TEAM_ID=$(value TEAM_ID)
APP_GROUP="$TEAM_ID.$BUNDLE_ID"  # as __init__.py's APP_GROUP: the API socket's folder (platforms: api_dir)
# Both are signed into the app group, so the engine may make the socket there and the app's Safari
# extension (sandboxed) may reach it. Not sandboxed themselves.
entitlements() {
cat <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>com.apple.security.application-groups</key><array><string>$APP_GROUP</string></array>
</dict></plist>
PLIST
}
# The Safari extension must be sandboxed; the app group is the one folder it can reach (the socket).
safari_entitlements() {
cat <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>com.apple.security.app-sandbox</key><true/>
  <key>com.apple.security.application-groups</key><array><string>$APP_GROUP</string></array>
</dict></plist>
PLIST
}
if [ "${1:-}" = "--entitlements" ]; then entitlements; exit 0; fi
VERSION=$(head -n 1 "$PROJECT/VERSION")  # the version: the first line of VERSION only
[ -n "$NAME" ] && [ -n "$VERSION" ] && [ -n "$BUNDLE_ID" ] && [ -n "$TEAM_ID" ] || { echo "no NAME, BUNDLE_ID or TEAM_ID in $IDENTITY, or no version in $PROJECT/VERSION" >&2; exit 1; }
OUT=${OUT:-$HOME/Applications/$NAME.app}
ICON="$PROJECT/packaging/icon/AppIcon.icns"

[ -x "$PYTHON" ] || { echo "no python at $PYTHON" >&2; exit 1; }
[ -f "$ICON" ] || { echo "no icon: run packaging/icon/make_icon.py first" >&2; exit 1; }
[ ! -e "$OUT" ] || trash "$OUT"  # an older build goes to the Trash, never deleted
mkdir -p "$OUT/Contents/MacOS" "$OUT/Contents/Resources"
swiftc -O -o "$OUT/Contents/MacOS/$NAME" "$HERE/Host.swift"
sed -e "s#__NAME__#$NAME#g" -e "s#__BUNDLE_ID__#$BUNDLE_ID#g" -e "s#__VERSION__#$VERSION#g" -e "s#__PYTHON__#$PYTHON#" -e "s#__PROJECT__#$PROJECT#" \
    "$HERE/Info.plist" > "$OUT/Contents/Info.plist"
cp "$ICON" "$OUT/Contents/Resources/AppIcon.icns"
# The helpers: Python's own app binary under the names "Waudio Engine" (the backend) and "Waudio
# Models" (a model worker, started per loaded model: backend/waudio/worker), each in a small app of its
# own, so Activity Monitor shows those names and the icon instead of "Python", and which one holds a
# model's memory. They run $PYTHON's venv (the host and the engine set __PYVENV_LAUNCHER__) and load
# Python through Homebrew's stable opt/ path, so a Python patch update needs no rebuild.
ENGINE="$OUT/Contents/Helpers/$NAME Engine.app"
MODELS="$OUT/Contents/Helpers/$NAME Models.app"
FRAMEWORK=$(cd "$(dirname "$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$PYTHON")")/.." && pwd)
PYAPP="$FRAMEWORK/Resources/Python.app/Contents/MacOS/Python"
[ -x "$PYAPP" ] || { echo "no framework Python app at $PYAPP" >&2; exit 1; }
LINKED=$(otool -L "$PYAPP" | awk 'NR > 1 && /Python.framework/ {print $1}')
STABLE=$(echo "$LINKED" | sed -E 's#/Cellar/(python@[0-9.]+)/[^/]+/#/opt/\1/#')
helper() {  # helper <its name after "$NAME "> <its bundle id suffix>
    local app="$OUT/Contents/Helpers/$NAME $1.app"
    mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
    cp "$PYAPP" "$app/Contents/MacOS/$NAME $1"
    [ -f "$STABLE" ] && install_name_tool -change "$LINKED" "$STABLE" "$app/Contents/MacOS/$NAME $1" 2>/dev/null
    cp "$ICON" "$app/Contents/Resources/AppIcon.icns"
    sed -e "s#__NAME__#$NAME#g" -e "s#__HELPER__#$1#g" -e "s#__HELPER_ID__#$2#g" -e "s#__BUNDLE_ID__#$BUNDLE_ID#g" \
        -e "s#__VERSION__#$VERSION#g" "$HERE/Helper-Info.plist" > "$app/Contents/Info.plist"
}
helper Engine engine
helper Models models
# Waudio for Safari: browser/ as a Safari web extension, its native handler passing requests to the
# API socket (SafariExtension.swift, APISocket.swift). Named "… for Safari" here; Chrome-only keys
# (the id key, the minimum Chrome version) left out. Safari finds it once the app has been opened.
SAFARI="$OUT/Contents/PlugIns/$NAME for Safari.appex"
mkdir -p "$SAFARI/Contents/MacOS" "$SAFARI/Contents/Resources"
swiftc -O -module-name SafariExtension -parse-as-library -application-extension -framework SafariServices \
    -Xlinker -e -Xlinker _NSExtensionMain -o "$SAFARI/Contents/MacOS/$NAME for Safari" "$HERE/APISocket.swift" "$HERE/SafariExtension.swift"
sed -e "s#__NAME__#$NAME#g" -e "s#__BUNDLE_ID__#$BUNDLE_ID#g" -e "s#__VERSION__#$VERSION#g" -e "s#__APP_GROUP__#$APP_GROUP#g" \
    "$HERE/SafariExtension-Info.plist" > "$SAFARI/Contents/Info.plist"
rsync -a --exclude tools/ --exclude _metadata/ --exclude README.md "$PROJECT/browser/" "$SAFARI/Contents/Resources/"
# The same extension for Chrome and other Chromium browsers to load (Settings → Permissions →
# Browsers → Set up shows this folder): it comes with the app, so a new user needs no checkout.
rsync -a --exclude tools/ --exclude _metadata/ "$PROJECT/browser/" "$OUT/Contents/Resources/browser/"
/usr/bin/python3 - "$SAFARI/Contents/Resources/manifest.json" <<'PY'
import json, sys
p = sys.argv[1]; m = json.load(open(p))
m["name"] = m["name"].replace(" for Chrome", " for Safari")
for chrome_only in ("key", "minimum_chrome_version"):
    m.pop(chrome_only, None)
json.dump(m, open(p, "w"), indent=2, ensure_ascii=False)
PY

# Sign with your Apple Development identity when there is one: macOS then knows the app by that
# identity, so permissions survive every rebuild. Ad-hoc ("-") ties them to this exact build:
# a rebuilt host leaves a stale record that makes macOS silently refuse to ask again.
SIGN_ID=${SIGN_ID:-$(security find-identity -v -p codesigning | sed -n 's/.*"\(Apple Development: [^"]*\)".*/\1/p' | head -1)}
SIGN_ID=${SIGN_ID:--}
echo "signing with: $SIGN_ID"
ENTITLEMENTS=$(mktemp -t waudio-entitlements)
trap 'for f in "$ENTITLEMENTS" "$ENTITLEMENTS.safari"; do [ ! -e "$f" ] || trash "$f"; done' EXIT  # also when signing fails
[ "$SIGN_ID" != "-" ] || echo "warning: no Apple Development certificate: the app group needs one — $NAME for Safari and the API socket won't work" >&2
entitlements > "$ENTITLEMENTS"
safari_entitlements > "$ENTITLEMENTS.safari"
codesign --force --sign "$SIGN_ID" --identifier "$BUNDLE_ID.safari" --options runtime --entitlements "$ENTITLEMENTS.safari" "$SAFARI"
codesign --force --sign "$SIGN_ID" --identifier "$BUNDLE_ID.engine" --entitlements "$ENTITLEMENTS" "$ENGINE"
codesign --force --sign "$SIGN_ID" --identifier "$BUNDLE_ID.models" "$MODELS"  # no app group: it talks to the engine only
codesign --force --sign "$SIGN_ID" --identifier "$BUNDLE_ID" --entitlements "$ENTITLEMENTS" "$OUT"
codesign --verify --strict --deep "$OUT"
echo "built $OUT ($NAME v$VERSION, $BUNDLE_ID)"
echo "  python:  $PYTHON (as $ENGINE)"
echo "  project: $PROJECT"
codesign -dvvv "$OUT" 2>&1 | grep -E "^(Identifier|CDHash)="
# the installed app: the Chrome helper's copies follow this checkout (same extensions allowed)
[ "$OUT" != "$HOME/Applications/$NAME.app" ] || sh "$PROJECT/packaging/chrome/install.sh" --refresh
