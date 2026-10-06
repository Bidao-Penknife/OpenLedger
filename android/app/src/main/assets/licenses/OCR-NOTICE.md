# Bundled Chinese OCR and Android runtime additions

Copyright Google LLC and respective upstream contributors.
The ML Kit Chinese and Latin recognition models and Google runtime components
are third-party software; OpenLedger's MIT license does not relicense them.
Use of ML Kit APIs and their related models is subject to the ML Kit terms:
https://developers.google.com/ml-kit/terms
The Google Play Services and ODML artifacts declare the Android SDK terms:
https://developer.android.com/studio/terms.html

DataTransport, Firebase components, Guava listenablefuture and javax.inject
are Apache-2.0; the full Apache license is preserved in Kotlin.txt and original
artifact notices are included when supplied. Exact locked coordinates, original
artifact/POM SHA256 digests and license declarations are in ocr-runtime-sources.json.
These runtime dependencies do not constitute a Firebase account, analytics
integration, cloud ledger or installed Google Play Services requirement.

The images and recognized text are processed on the device. The SDK can send
application/device/performance diagnostics to Google. OpenLedger defers its
initialization until OCR is used and shows a disclosure before selecting an image.
https://developers.google.com/ml-kit/android-data-disclosure
