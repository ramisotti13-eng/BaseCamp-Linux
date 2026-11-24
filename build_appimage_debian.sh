#!/usr/bin/env bash
# Build the Debian-compatible AppImage in a container (glibc 2.36).
#
#   ./build_appimage_debian.sh                  builder image, then the AppImage in it
#                                               (Docker; CONTAINER_ENGINE=podman for Podman,
#                                               BUILDER_IMAGE=<name> for another image name)
#   bash build_appimage_debian.sh --in-container
#                                               the second half alone, run inside the
#                                               builder image (what the first form runs;
#                                               also the way to build from a Windows host)
#
# Produces BaseCamp-Linux-x86_64-debian.AppImage at the repo root. The Fedora
# build is made natively (see the .spec files + appimagetool); this one needs a
# Debian environment for the older glibc, hence the container.
#
# The AppImage carries the web interface's window (pywebview with Qt
# WebEngine through PySide6, see Dockerfile.debian) besides the classic
# CustomTkinter one. It runs on a machine without FUSE with
# --appimage-extract-and-run (or APPIMAGE_EXTRACT_AND_RUN=1).
set -euo pipefail
cd "$(dirname "$0")"

OUT=BaseCamp-Linux-x86_64-debian.AppImage
SPECS="BaseCamp-Linux basecamp-controller basecamp-tray everest60-controller makalu-controller"

if [ "${1:-}" != "--in-container" ]; then
    # basecamp4-: the 3.x builder image (basecamp-debian-builder) has no Qt
    # WebEngine, and a machine that builds both keeps both.
    IMAGE=${BUILDER_IMAGE:-basecamp4-debian-builder}
    ENGINE=${CONTAINER_ENGINE:-docker}
    if ! command -v "$ENGINE" >/dev/null 2>&1; then
        echo "!! $ENGINE not found: install Docker, or set CONTAINER_ENGINE=podman" >&2
        exit 1
    fi
    # Docker writes the AppImage as root, so it is handed back to the owner of
    # this folder. Rootless Podman already writes as you (a chown inside would
    # give it to a mapped user id instead).
    OWNER_ENV=(-e OWNER="$(stat -c %u:%g .)")
    MOUNT_OPT=""
    if [ "$(basename "$ENGINE")" = "podman" ]; then
        OWNER_ENV=()
        MOUNT_OPT=":z"           # SELinux (Fedora): let the container read the folder
    fi
    echo ">> Building builder image ($IMAGE) with $ENGINE…"
    "$ENGINE" build -f Dockerfile.debian -t "$IMAGE" .

    echo ">> Building binaries + AppImage inside the container…"
    "$ENGINE" run --rm -v "$PWD":/work$MOUNT_OPT -w /work "${OWNER_ENV[@]}" \
        "$IMAGE" bash build_appimage_debian.sh --in-container
    echo ">> Done: $OUT"
    exit 0
fi

# ── Inside the builder ──────────────────────────────────────────────────────
# PyInstaller's work and dist directories live in the container's own file
# system: on a bind mount (Docker Desktop on Windows above all) the tens of
# thousands of small files Qt WebEngine brings make the build crawl. Only the
# finished AppImage is copied back.
WORK=${BUILD_TMP:-/tmp/basecamp-appimage}
rm -rf "$WORK"
mkdir -p "$WORK"

for spec in $SPECS; do
    echo "--- pyinstaller ${spec}.spec ---"
    pyinstaller --noconfirm --clean \
        --distpath "$WORK/dist" --workpath "$WORK/build" "${spec}.spec"
done

APPDIR="$WORK/AppDir"
mkdir -p "$APPDIR/usr/bin"
cp -a "$WORK/dist/BaseCamp-Linux/." "$APPDIR/usr/bin/"
for bin in basecamp-controller basecamp-tray everest60-controller makalu-controller; do
    cp -a "$WORK/dist/$bin" "$APPDIR/usr/bin/"
done

# AppRun, desktop entry and icon. Written here: they used to be copied from a
# local AppDir/, which is git-ignored, so a fresh checkout could not build.
# The same three files as in the 3.x AppImages.
cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/bash
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/BaseCamp-Linux" "$@"
EOF
cat > "$APPDIR/basecamp-linux.desktop" <<'EOF'
[Desktop Entry]
Name=BaseCamp Linux
Comment=Mountain Everest Max display control for Linux
Exec=BaseCamp-Linux
Icon=basecamp-linux
Type=Application
Categories=Utility;
EOF
cp resources/app_icon_256.png "$APPDIR/basecamp-linux.png"
chmod +x "$APPDIR/AppRun" "$APPDIR/usr/bin/BaseCamp-Linux"
for bin in basecamp-controller basecamp-tray everest60-controller makalu-controller; do
    chmod +x "$APPDIR/usr/bin/$bin"
done

ARCH=x86_64 appimagetool --no-appstream "$APPDIR" "$WORK/$OUT"
cp "$WORK/$OUT" "./$OUT"
if [ -n "${OWNER:-}" ]; then
    chown "$OWNER" "./$OUT"
fi
rm -rf "$WORK"
ls -l "./$OUT"
