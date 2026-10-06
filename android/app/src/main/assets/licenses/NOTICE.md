# OpenLedger Android runtime notices

OpenLedger code: MIT; see OpenLedger.txt.
Chaquopy 17.0.0: MIT; see Chaquopy.txt.
Python 3.12.12: PSF and historical Python licenses; see CPython.txt.
Kotlin 2.2.21: Apache-2.0; see Kotlin.txt.
OpenSSL 3.0.18: Apache-2.0; see OpenSSL.txt.
tzdata 2026.4 packaging: see tzdata.txt; IANA timezone data is public domain.
SQLite: public domain; see SQLite.txt.

The Python standard library includes native compression and FFI components.
Their upstream notices are preserved in libffi.txt, bzip2.txt, zlib.txt, and
xz.txt. The xz upstream COPYING material distinguishes liblzma from other
utilities in the source distribution; the APK embeds liblzma rather than those
command-line utilities. The associated LGPL text is included for completeness.

Original source locations and SHA256 digests are in sources.json. These runtime
notices do not include a JDK, Android SDK, Gradle, or formatter in the APK.

Spreadsheet exchange adds openpyxl 3.1.5, et-xmlfile 2.0.0 and defusedxml 0.7.1;
release version comparisons add packaging 26.3. HTTPS uses Chaquopy's certifi
2025.8.3 CA bundle. Their original license files and wheel/source digests are
preserved alongside this notice in mobile-feature-sources.json. Financial
reports use Android's native graphics APIs rather than bundling Qt.
