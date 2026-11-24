# Building the AppImage yourself

The release page has a ready AppImage. If you would rather build it from the
source yourself, this page shows how, on Arch and on Debian or Ubuntu. The
result is the same file the release ships: one AppImage that runs on Arch,
Debian, Ubuntu, Mint, Fedora and most other distributions.

The build runs in a container (Debian 12), so nothing is installed on your
system apart from Docker or Podman, and the AppImage works on older
distributions too. You need:

- `git`, and **Docker** or **Podman**
- about 4 GB of free disk space (the build image) and an internet connection
  for the first build, which downloads about 1 GB
- a few minutes: about three on a fast connection; later builds reuse the
  image and are quicker

## 1. Install the tools

### Arch, CachyOS, Manjaro

With Docker:

```bash
sudo pacman -S --needed git docker
sudo systemctl enable --now docker.service
sudo usermod -aG docker "$USER"
```

Log out and back in once, so that the `docker` group applies.

Or with Podman, which needs no service and no group:

```bash
sudo pacman -S --needed git podman
```

### Debian, Ubuntu, Linux Mint

With Docker:

```bash
sudo apt install git docker.io
sudo usermod -aG docker "$USER"
```

Log out and back in once, so that the `docker` group applies.

Or with Podman:

```bash
sudo apt install git podman
```

## 2. Get the source

The 4.0 beta is on the `v4-beta` branch:

```bash
git clone -b v4-beta https://github.com/ramisotti13-eng/BaseCamp-Linux.git
cd BaseCamp-Linux
```

## 3. Build

With Docker:

```bash
./build_appimage_debian.sh
```

With Podman:

```bash
CONTAINER_ENGINE=podman ./build_appimage_debian.sh
```

The script builds the image `basecamp4-debian-builder` first (only the first
time takes long), then the application inside it. At the end it prints
`>> Done: BaseCamp-Linux-x86_64-debian.AppImage`, and the file is in the
folder, owned by you. The lines `WARNING: Failed to collect submodules for
'pystray'` on the way are harmless: the build container has no display.

## 4. Run it

Quit a running BaseCamp Linux 3.x first (tray icon, Quit). Back up your
settings once, since 4.0 uses the same files and takes your 3.x profiles over:

```bash
cp -a ~/.config/mountain-time-sync ~/mts-backup
chmod +x BaseCamp-Linux-x86_64-debian.AppImage
./BaseCamp-Linux-x86_64-debian.AppImage
```

The devices need the udev rule once (also for the release AppImage). It is in
the folder you cloned:

```bash
sudo cp 99-mountain.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```

Then unplug the devices and plug them in again. The rule also lets the
application read the Everest Max's own keys, which binding a key to an
application action needs. More in the README, under
[USB permissions](../README.md#usb-permissions-required-once-appimage--source-installs).

## Updating

```bash
git pull
./build_appimage_debian.sh          # or: CONTAINER_ENGINE=podman ./build_appimage_debian.sh
```

## If something goes wrong

| What you see | What to do |
|---|---|
| `permission denied while trying to connect to the Docker daemon socket` | You are not in the `docker` group yet: log out and back in after `usermod`, or use Podman. |
| `docker: command not found` while Podman is installed | Use `CONTAINER_ENGINE=podman ./build_appimage_debian.sh`. |
| Podman: `potentially insufficient UIDs or GIDs available` | Your user has no subordinate ids: `sudo usermod --add-subuids 100000-165535 --add-subgids 100000-165535 "$USER"`, then `podman system migrate`. |
| The AppImage does not start and names FUSE | Start it with `--appimage-extract-and-run`, or install FUSE (`fuse2` on Arch, `libfuse2` or `libfuse2t64` on Debian and Ubuntu). |
| No space left | The build needs about 4 GB. Remove the image afterwards with `docker image rm basecamp4-debian-builder` (or `podman image rm`). |

## Without an AppImage

You can also run the application straight from the source with the Python of
your distribution: see [From source](../README.md#from-source) in the README.
Clone the `v4-beta` branch for 4.0 (`git clone -b v4-beta ...`).
