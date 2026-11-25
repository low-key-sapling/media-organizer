#!/usr/bin/env bash
# Build-time Linux dependency. End users do not run this script or install Perl.
set -euo pipefail
project_root="$(cd "$(dirname "$0")/.." && pwd)"
perl_version="5.42.0"
work_dir="$project_root/.cache/perl-build"
prefix_dir="$project_root/resources/vendor/perl"
mkdir -p "$work_dir" "$prefix_dir"
if [ -x "$prefix_dir/bin/perl" ]; then
  "$prefix_dir/bin/perl" -e 'print "Bundled Perl ready\n"'
  exit 0
fi
cd "$work_dir"
curl --fail --location --retry 3 --output "perl-$perl_version.tar.xz" "https://www.cpan.org/src/5.0/perl-$perl_version.tar.xz"
tar -xf "perl-$perl_version.tar.xz"
cd "perl-$perl_version"
sh Configure -des -Dprefix="$prefix_dir" -Duserelocatableinc -Uuseshrplib -Dman1dir=none -Dman3dir=none
make -j2
make install
cp Copying Artistic README "$prefix_dir/"
mkdir -p "$prefix_dir/lib/runtime"
while IFS= read -r dependency; do
  case "$(basename "$dependency")" in
    libc.so.*|ld-linux*|libm.so.*|libdl.so.*|libpthread.so.*|librt.so.*) ;;
    *) cp -L "$dependency" "$prefix_dir/lib/runtime/" ;;
  esac
done < <( { ldd "$prefix_dir/bin/perl"; find "$prefix_dir/lib" -name '*.so' -type f -exec ldd {} \;; } | awk '/=> \/.* \(/ {print $3}' | sort -u)
"$prefix_dir/bin/perl" -MFile::Basename -MEncode -MJSON::PP -e 'print "Relocatable Perl and core modules ready\n"'
