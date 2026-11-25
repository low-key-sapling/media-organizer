# Third-party resources

The application works offline. Downloads below happen only on the build machine.

- **GeoNames** — https://www.geonames.org/ — city data under **CC BY 4.0** (https://creativecommons.org/licenses/by/4.0/). Source files: cities500.zip, admin1CodesASCII.txt, countryInfo.txt. Modifications: field selection, alias indexing, ASCII filename normalization and SQLite conversion. Data version, build timestamp, download URLs and SHA-256 are in the bundled `data/places.sqlite3` and `data/resources.json`. City-point proximity is approximate and is not an administrative-boundary determination. GeoNames does not guarantee accuracy or completeness.
- **ExifTool**, Phil Harvey — https://exiftool.org/ — distributed under the same terms as Perl (Artistic License / GNU GPL). Bundled upstream files and notices are retained. Windows packaging and included Perl distribution: Oliver Betz, https://oliverbetz.de/pages/Artikel/ExifTool-for-Windows .
- **Perl** — https://www.perl.org/ — Artistic License / GNU GPL; Linux releases carry the matching relocatable runtime and its `Copying` / `Artistic` files. The corresponding upstream source is https://www.cpan.org/src/5.0/ and the exact build recipe is `scripts/build_perl.sh`.
- **Python** — https://www.python.org/ — Python Software Foundation License; the frozen application includes Python and its required runtime libraries.
- **FastAPI / Uvicorn / Vue / Vite / TypeScript / Lucide** — respective upstream licenses. Vue, Lucide and required interface code are compiled into the local interface. Vite, TypeScript and Node are build-time tools, not user prerequisites.
- **Pillow** — https://python-pillow.github.io/ — HPND license and notices for its bundled libraries, retained by the freezer.
- **SQLite** — https://www.sqlite.org/ — public domain.
- **PyInstaller** — https://pyinstaller.org/ — GPL with the documented bootloader exception allowing distribution of frozen applications under their own license.

The license in this repository applies to the application code, not to the third-party resources. No bundled third-party source has been modified except for the documented city database conversion and build configuration of Perl.
