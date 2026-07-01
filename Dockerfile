#7 ERROR: process "/bin/sh -c apt-get update && apt-get install -y --no-install-recommends     tesseract-ocr     tesseract-ocr-es     libgl1-mesa-glx     libglib2.0-0     && apt-get clean     && rm -rf /var/lib/apt/lists/*" did not complete successfully: exit code: 100
------
 > [2/6] RUN apt-get update && apt-get install -y --no-install-recommends     tesseract-ocr     tesseract-ocr-es     libgl1-mesa-glx     libglib2.0-0     && apt-get clean     && rm -rf /var/lib/apt/lists/*:
2.077 Reading state information...
2.096 Package libgl1-mesa-glx is not available, but is referred to by another package.
2.096 This may mean that the package is missing, has been obsoleted, or
2.096 is only available from another source
2.096 
2.100 E: Unable to locate package tesseract-ocr-es
2.100 E: Package 'libgl1-mesa-glx' has no installation candidate
------
Dockerfile:10
--------------------
   9 |     # Usamos tesseract-ocr-es (nombre correcto en Debian) y agregamos tolerancia a fallos de red
  10 | >>> RUN apt-get update && apt-get install -y --no-install-recommends \
  11 | >>>     tesseract-ocr \
  12 | >>>     tesseract-ocr-es \
  13 | >>>     libgl1-mesa-glx \
  14 | >>>     libglib2.0-0 \
  15 | >>>     && apt-get clean \
  16 | >>>     && rm -rf /var/lib/apt/lists/*
  17 |     
--------------------
error: failed to solve: process "/bin/sh -c apt-get update && apt-get install -y --no-install-recommends     tesseract-ocr     tesseract-ocr-es     libgl1-mesa-glx     libglib2.0-0     && apt-get clean     && rm -rf /var/lib/apt/lists/*" did not complete successfully: exit code: 100
