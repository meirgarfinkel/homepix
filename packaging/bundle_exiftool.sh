#!/usr/bin/env bash
# Makes $1 (a folder) hold ExifTool with a Perl of its own, for the bundled app:
#   exiftool, lib/          ExifTool itself (pinned below, checked against its published checksum)
#   perl/bin/perl, perl/lib the Perl interpreter of this machine, with only the modules ExifTool uses
#   run.pl                  starts ExifTool with Perl's module path limited to the bundle, so a
#                           different Perl installed on the computer is never mixed in
# Needs perl on this machine (release builds: AlmaLinux 9's) and python with Pillow (to make the
# sample photos it checks itself on).   Usage: bundle_exiftool.sh OUT_DIR PYTHON
set -euo pipefail

VERSION=13.59  # update both lines together (checksums: https://exiftool.org/checksums.txt)
SHA256=668ea3acececb7235fbd0f4900e72d5f12c9b07e5c778fd36cb1e9b5828fd65a
OUT="$1"
PYTHON="${2:-python3}"
CACHE="$(dirname "$OUT")/downloads"
TARBALL="$CACHE/Image-ExifTool-$VERSION.tar.gz"

mkdir -p "$CACHE"
if [ ! -f "$TARBALL" ] || ! echo "$SHA256  $TARBALL" | sha256sum -c --quiet - 2>/dev/null; then
  curl -fsSL -o "$TARBALL.part" "https://sourceforge.net/projects/exiftool/files/Image-ExifTool-$VERSION.tar.gz/download"
  mv "$TARBALL.part" "$TARBALL"
fi
echo "$SHA256  $TARBALL" | sha256sum -c --quiet - || { echo "ExifTool download doesn't match its checksum"; exit 1; }

rm -rf "$OUT"
mkdir -p "$OUT/perl/bin" "$OUT/perl/lib" "$OUT/perl/so"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
tar xzf "$TARBALL" -C "$WORK"
SRC="$WORK/Image-ExifTool-$VERSION"
cp "$SRC/exiftool" "$OUT/exiftool"
cp -r "$SRC/lib" "$OUT/lib"
find "$OUT/lib" -name "*.pod" -delete

# The launcher: only the bundle's modules (ExifTool's own lib/ is added by ExifTool itself)
cat > "$OUT/run.pl" <<'PERL'
BEGIN {
    my ($dir) = __FILE__ =~ m{^(.*)/[^/]*$};
    @INC = ("$dir/lib", "$dir/perl/lib");
    $0 = "$dir/exiftool";
}
do $0;
die $@ if $@;
PERL

# Sample files of every kind it writes, to see which Perl modules it loads
mkdir -p "$WORK/samples"
"$PYTHON" - "$WORK/samples" <<'PY'
import sys
from PIL import Image
out = sys.argv[1]
im = Image.new("RGB", (64, 48), (200, 30, 30))
for ext in ("jpg", "png", "tif", "webp", "gif"):
    im.save(f"{out}/a.{ext}")
im.save(f"{out}/a.dng", "TIFF", tiffinfo={50706: (1, 4, 0, 0)})
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    ex = Image.Exif(); ex[0x112] = 6
    im.save(f"{out}/a.heic", exif=ex.tobytes())
except ImportError:
    pass
PY
exercise() {  # $@: how to run ExifTool. Reads and writes everything the app does.
  local s="$WORK/samples"
  for f in "$s"/*; do
    "$@" -j -a -G1 -s -struct -n "$f" >/dev/null
    "$@" -m -P -overwrite_original '-XMP-dc:Description=héllo\nworld' -XMP-dc:Subject=a -XMP-dc:Subject=b \
      -XMP-xmp:Rating#=3 '-ExifIFD:DateTimeOriginal=2020:01:02 03:04:05' -GPS:GPSLatitude#=31.5 \
      -GPS:GPSLatitudeRef=N -IFD0:Orientation#=6 -IPTC:Keywords=k "$f" >/dev/null 2>&1 || true
    "$@" -m -P -overwrite_original -QuickTime:Rotation#=90 "$f" >/dev/null 2>&1 || true
  done
  printf -- '-ver\n-execute1\n-stay_open\nFalse\n' | "$@" -stay_open True -@ - -common_args -charset UTF8 >/dev/null
}

PERL_BIN="$(command -v perl)"
cat > "$WORK/Trace.pm" <<'PERL'
package Trace; END { open my $f, ">>", $ENV{TRACE_OUT} or return; print $f "$_\t$INC{$_}\n" for keys %INC }
1;
PERL
export TRACE_OUT="$WORK/inc.txt"
exercise "$PERL_BIN" -I"$WORK" -MTrace "$OUT/exiftool"
# Modules loaded only now and then, in some files or with some options
EXTRA="POSIX.pm Digest/MD5.pm Digest/SHA.pm Time/HiRes.pm Time/Local.pm Encode/Unicode.pm Encode/Byte.pm
       Unicode/Normalize.pm File/Temp.pm File/Spec.pm File/Spec/Unix.pm File/Path.pm Cwd.pm
       Compress/Zlib.pm IO/Uncompress/Gunzip.pm IO/Compress/Gzip.pm Storable.pm"
{
  cut -f1 "$WORK/inc.txt"
  for m in $EXTRA; do echo "$m"; done
} | grep -v "^Image/ExifTool\|^File/RandomAccess\|^Trace.pm" | sort -u > "$WORK/modules.txt"

INCDIRS="$("$PERL_BIN" -e 'print join("\n", grep { !ref && -d } @INC)')"
copy_module() {  # Foo/Bar.pm, with its compiled part (auto/Foo/Bar/Bar.so) if it has one
  local m="$1" d
  for d in $INCDIRS; do
    if [ -f "$d/$m" ]; then
      mkdir -p "$OUT/perl/lib/$(dirname "$m")"
      cp "$d/$m" "$OUT/perl/lib/$m"
      local auto="$d/auto/${m%.pm}"
      if [ -d "$auto" ]; then
        mkdir -p "$OUT/perl/lib/auto/${m%.pm}"
        # only its own files: subfolders belong to other modules (auto/Encode/JP is Encode::JP)
        find "$auto" -maxdepth 1 -type f -exec cp {} "$OUT/perl/lib/auto/${m%.pm}/" \;
      fi
      return 0
    fi
  done
  return 1
}
# Modules pull in others: repeat until nothing new turns up
while read -r m; do copy_module "$m" || echo "     (no $m in this Perl: skipped)"; done < "$WORK/modules.txt"
for _ in 1 2 3; do
  "$PERL_BIN" -e '
    BEGIN { @INC = ($ARGV[0]); }
    for my $m (split /\n/, $ARGV[1]) { eval { require $m; 1 } }
    print "$_\n" for sort keys %INC;' "$OUT/perl/lib" "$(cat "$WORK/modules.txt")" > "$WORK/have.txt" 2>/dev/null || true
  "$PERL_BIN" -e '
    for my $m (split /\n/, $ARGV[0]) { eval { require $m; 1 } }
    print "$_\n" for sort keys %INC;' "$(cat "$WORK/modules.txt")" > "$WORK/want.txt"
  comm -13 "$WORK/have.txt" "$WORK/want.txt" > "$WORK/missing.txt" || true
  [ -s "$WORK/missing.txt" ] || break
  while read -r m; do copy_module "$m" || true; done < "$WORK/missing.txt"
done
# The Unicode tables some regular expressions need
UNICORE="$(for d in $INCDIRS; do [ -d "$d/unicore" ] && echo "$d/unicore" && break; done)"
[ -n "$UNICORE" ] && cp -r "$UNICORE" "$OUT/perl/lib/unicore" && rm -rf "$OUT/perl/lib/unicore/"*.txt

# The interpreter, and the libraries it needs that aren't part of every Linux's C library
cp "$PERL_BIN" "$OUT/perl/bin/perl"
ldd "$PERL_BIN" | awk '/=> \//{print $3}' | while read -r lib; do
  case "$(basename "$lib")" in
    libc.so*|libm.so*|libdl.so*|libpthread.so*|librt.so*|libutil.so*|ld-linux*) ;;
    *) cp -L "$lib" "$OUT/perl/so/" ;;
  esac
done

# Check: with nothing but the bundle, ExifTool reads and writes every kind of file
run_bundled() { env -i PATH=/usr/bin:/bin LD_LIBRARY_PATH="$OUT/perl/so" "$OUT/perl/bin/perl" "$OUT/run.pl" "$@"; }
exercise run_bundled
got="$(run_bundled -s3 -XMP-dc:Description -XMP-xmp:Rating "$WORK/samples/a.jpg" | tr '\n' ' ')"
case "$got" in
  "héllo"*"3 "*) ;;
  *) echo "The bundled ExifTool didn't write what it should (got: $got)"; exit 1 ;;
esac
if run_bundled -ver 2>&1 | grep -q "Can't locate"; then echo "The bundled Perl is missing a module"; exit 1; fi
echo "     ExifTool $(run_bundled -ver) with Perl $("$OUT/perl/bin/perl" -e 'print $^V' 2>/dev/null || true), $(du -sh "$OUT" | cut -f1)"
