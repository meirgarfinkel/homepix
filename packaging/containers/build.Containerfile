# The machine the Linux AppImage is built on. AlmaLinux 9 has glibc 2.34: the AppImage runs on
# Linux systems with that or newer (Debian 12+, Ubuntu 22.04+, Fedora, Arch, ...).
FROM almalinux:9

RUN dnf -y install epel-release \
 && dnf -y install --setopt=install_weak_deps=False \
      python3.11 python3.11-pip \
      findutils file which binutils \
      # Qt's window system plugin needs these; bundling them saves people installing them
      xcb-util-cursor xcb-util-wm xcb-util-image xcb-util-keysyms xcb-util-renderutil \
      libxkbcommon-x11 libxkbcommon fontconfig freetype \
      mesa-libGL mesa-libEGL mesa-libgbm libdrm \
      nss nspr alsa-lib libXcomposite libXdamage libXrandr libXtst libxshmfence libxkbfile \
      dbus-libs glib2 \
 && dnf clean all

# appimagetool runs from a temporary folder without FUSE inside the container
ENV APPIMAGE_EXTRACT_AND_RUN=1 PYTHON=python3.11 GLIBC_MAX=2.34
