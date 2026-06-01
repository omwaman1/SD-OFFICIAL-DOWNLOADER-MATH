============================================================
  CLASSPLUS VIDEO DOWNLOADER - Complete Guide
============================================================

WHAT'S INCLUDED (tools/ folder):
  - adb.exe          → Android Debug Bridge
  - ffmpeg.exe       → Video muxing
  - mp4decrypt.exe   → DRM decryption
  - frida.exe        → Frida instrumentation

CURRENT VERSIONS (as of June 2026):
  - Python:       3.12.10
  - Frida:        17.9.11
  - frida-tools:  14.8.2
  - pywidevine:   1.9.0
  - Frida Gadget: 17.9.11 (must match frida version!)

PYTHON REQUIREMENTS (install once):
  1. Install Python 3.10+ from https://python.org
     (Check "Add to PATH" during install)
  2. Open CMD and run:
     pip install pywidevine requests frida-tools

TO USE ON A NEW PC:
  1. Copy the entire SACH folder
  2. Install Python 3.10+
  3. Run:  pip install pywidevine requests frida-tools
  4. Connect phone via USB
  5. Double-click 1_START_BRIDGE.bat
  6. Double-click 2_DOWNLOAD.bat
  That's it!

PHONE REQUIREMENTS:
  - USB Debugging enabled (Settings > Developer Options)
  - Frida Gadget patched Classplus APK installed
  - device.wvd file in SACH folder

============================================================
  HOW TO DOWNLOAD VIDEOS
============================================================

  Step 1: Connect phone via USB

  Step 2: Double-click  1_START_BRIDGE.bat
          → Waits for app to load
          → Starts Frida hooks
          → Keep this window OPEN!

  Step 3: Double-click  2_DOWNLOAD.bat
          → First run scans course folders (~30s, one time)
          → Shows "Waiting for video..."

  Step 4: In the Classplus app on phone:
          → Open a video (1st play = detection)
          → Script shows video name + duration
          → Script says "NOW REPLAY THE SAME VIDEO!"
          → Play the same video again
          → Keys captured → auto downloads!

  Step 5: After download completes:
          → Press ENTER in the CMD window
          → Play next video in app
          → Repeat!

  Videos saved to:  SACH\downloads\<folder>\<video name>.mp4
  Example: downloads\1.NEW LECTURES\1.तर्कक्षमता\...\video.mp4

============================================================
  FOLDER STRUCTURE
============================================================

  SACH/
  ├── CONNECT/
  │   ├── 1_START_BRIDGE.bat    ← Start here
  │   ├── 2_DOWNLOAD.bat        ← Then here
  │   ├── 3_REINDEX.bat         ← If new videos added
  │   ├── README.txt            ← This file
  │   └── tools/                ← All binaries
  │       ├── adb.exe
  │       ├── ffmpeg.exe
  │       ├── frida.exe
  │       └── mp4decrypt.exe
  ├── hook_combined.js          ← Frida hook script
  ├── play_and_save.py          ← Main downloader
  ├── device.wvd                ← Widevine device keys
  ├── video_index.json          ← Course index (auto-created)
  ├── downloads/                ← Saved videos go here
  └── temp/                     ← Temp files (auto-cleaned)

============================================================
  UPDATING PYTHON / FRIDA VERSION
============================================================

  ⚠️  IMPORTANT: The Frida Gadget inside the APK MUST match
  the frida-tools version on your PC. If you update Python
  or frida-tools, you MUST repack the APK with the matching
  Frida Gadget version.

  --- Step 1: Check your current Frida version ---

    frida --version
    Example output: 17.9.11

  --- Step 2: Update Python packages ---

    pip install --upgrade frida-tools pywidevine requests
    frida --version
    Note the NEW version (e.g. 17.10.0)

  --- Step 3: Download matching Frida Gadget ---

    Go to: https://github.com/ArcticFox-Pro/FridaGadget/releases
    Or:    https://github.com/ArcticFox-Pro/FridaGadget/releases

    Download the gadget matching your NEW frida version and phone arch:
      - For most phones: frida-gadget-<VERSION>-android-arm64.so
      - Example: frida-gadget-17.10.0-android-arm64.so

    Direct download URL pattern:
      https://github.com/ArcticFox-Pro/FridaGadget/releases/download/<VERSION>/frida-gadget-<VERSION>-android-arm64.so.xz

    Or from official Frida releases:
      https://github.com/frida/frida/releases/tag/<VERSION>
      Download: frida-gadget-<VERSION>-android-arm64.so.xz
      Extract the .xz file (use 7-Zip)

  --- Step 4: Decompile the APK ---

    Tools needed: apktool, uber-apk-signer (or apksigner)
    Download apktool: https://apktool.org/
    Download uber-apk-signer:
      https://github.com/ArcticFox-Pro/uber-apk-signer/releases

    a) Decompile:
       java -jar apktool.jar d classplus.apk -o classplus_decompiled

  --- Step 5: Replace Frida Gadget ---

    a) Go to: classplus_decompiled\lib\arm64-v8a\
       (or lib\armeabi-v7a\ for 32-bit phones)

    b) Find the existing gadget file. It will be named something like:
       - libfrida-gadget.so
       - libgadget.so
       - or any .so file ~30-40MB in size

    c) Delete the old gadget .so file

    d) Copy your new frida-gadget-<VERSION>-android-arm64.so here

    e) Rename it to the EXACT same name as the old file:
       Example: rename to libfrida-gadget.so

  --- Step 6: Rebuild & Sign the APK ---

    a) Rebuild:
       java -jar apktool.jar b classplus_decompiled -o classplus_patched.apk

    b) Sign (using uber-apk-signer):
       java -jar uber-apk-signer.jar -a classplus_patched.apk

       This creates: classplus_patched-aligned-debugSigned.apk

    c) Or sign with apksigner:
       First create a keystore (one time):
         keytool -genkey -v -keystore my.keystore -alias key -keyalg RSA -keysize 2048 -validity 10000

       Then sign:
         apksigner sign --ks my.keystore classplus_patched.apk

  --- Step 7: Install on phone ---

    a) Uninstall old app first:
       adb uninstall co.shield.yyxdj

    b) Install new APK:
       adb install classplus_patched-aligned-debugSigned.apk

    c) Open the app, login again

  --- Step 8: Verify ---

    Run 1_START_BRIDGE.bat
    You should see: "Connected to SM M346B"
    And all hooks should show [+]

  --- Quick Version Check ---

    To verify everything matches:
      frida --version          → should show e.g. 17.10.0
      (in Frida console)       → Frida 17.10.0
    Both must show the SAME version number!

============================================================
  UTILITIES
============================================================

  3_REINDEX.bat
    → Deletes video_index.json cache
    → Run this if new videos were added to the course
    → Next download run will re-scan all folders

============================================================
  TROUBLESHOOTING
============================================================

  "Bridge not running"
    → Run 1_START_BRIDGE.bat first and keep it open

  "No device found"
    → Check USB cable
    → Enable USB Debugging on phone
    → Accept the USB debugging prompt on phone

  "Timeout - no keys captured"
    → App cached old DRM session
    → Close the video, go back, open it again
    → Or: close app completely, run 1_START_BRIDGE again

  "Video detected" shows wrong/old video
    → Press ENTER to skip, play the correct video

  "Failed to attach: unexpected error"
    → Frida version mismatch! See UPDATING section above
    → Your frida-tools version must match the Gadget in APK

  Video plays in VLC but not Windows Media Player
    → Already fixed! Audio is re-encoded to AAC
    → For old downloads:
       ffmpeg -y -i old.mp4 -c:v copy -c:a aac -movflags +faststart fixed.mp4

  Want to re-scan course folders
    → Double-click 3_REINDEX.bat
    → Or delete video_index.json manually

============================================================
