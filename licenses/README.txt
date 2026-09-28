Video Compressor - license delivery contents

The build scripts assemble this directory without downloading anything.

1. FFmpeg
   FFMPEG_SOURCE.txt records the upstream and binary-provider source locations.
   FFMPEG_LICENSE_AND_SOURCE.txt is generated at build time from the exact
   selected ffmpeg/ffprobe inputs. It includes their -L license output,
   versions, configurations, and pre-signing SHA256 values. BUILD_MANIFEST.txt
   is authoritative for final delivered hashes after platform signing.

2. Python distributions
   PYTHON_PACKAGE_LICENSES.txt and versioned package subdirectories are
   generated from the interpreter used for the build. The scripts locate each
   distribution with importlib.metadata and copy license files from its
   dist-info directory. This includes both a licenses subdirectory and root
   files such as LICENSE or COPYING. No python/ site-packages path is
   hard-coded. The Windows build also delivers psutil 7.2.2's BSD-3-Clause
   LICENSE text.

The generated delivery must keep this directory, README.txt,
THIRD_PARTY_NOTICES.txt, and BUILD_MANIFEST.txt together.
