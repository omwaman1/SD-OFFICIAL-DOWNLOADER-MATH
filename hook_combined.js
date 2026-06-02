// Combined Frida script: USB bypass + HTTP bridge + DRM key interception
// Strategy: Replace getKeyRequest challenge with ours, capture provideKeyResponse
Java.perform(function () {
    // =============================================
    // PART 1: USB DEBUGGING / DEVELOPER MODE BYPASS
    // =============================================

    // --- Settings.Secure / Settings.Global ---
    var S = Java.use("android.provider.Settings$Secure");
    S.getInt.overload("android.content.ContentResolver", "java.lang.String", "int").implementation = function (a, b, c) {
        if (b === "adb_enabled" || b === "development_settings_enabled") return 0;
        return this.getInt(a, b, c);
    };
    S.getInt.overload("android.content.ContentResolver", "java.lang.String").implementation = function (a, b) {
        if (b === "adb_enabled" || b === "development_settings_enabled") return 0;
        return this.getInt(a, b);
    };
    S.getString.overload("android.content.ContentResolver", "java.lang.String").implementation = function (a, b) {
        if (b === "adb_enabled" || b === "development_settings_enabled") return "0";
        return this.getString(a, b);
    };
    var G = Java.use("android.provider.Settings$Global");
    G.getInt.overload("android.content.ContentResolver", "java.lang.String", "int").implementation = function (a, b, c) {
        if (b === "adb_enabled" || b === "development_settings_enabled") return 0;
        return this.getInt(a, b, c);
    };
    G.getInt.overload("android.content.ContentResolver", "java.lang.String").implementation = function (a, b) {
        if (b === "adb_enabled" || b === "development_settings_enabled") return 0;
        return this.getInt(a, b);
    };
    G.getString.overload("android.content.ContentResolver", "java.lang.String").implementation = function (a, b) {
        if (b === "adb_enabled" || b === "development_settings_enabled") return "0";
        return this.getString(a, b);
    };

    // --- Debug class ---
    var D = Java.use("android.os.Debug");
    D.isDebuggerConnected.implementation = function () { return false; };

    // --- Build properties (ro.debuggable, userdebug, etc.) ---
    try {
        var Build = Java.use("android.os.Build");
        Build.TYPE.value = "user";
        Build.TAGS.value = "release-keys";
    } catch(e) {}

    // --- SystemProperties (ro.debuggable, ro.secure, etc.) ---
    try {
        var SP = Java.use("android.os.SystemProperties");
        SP.get.overload("java.lang.String", "java.lang.String").implementation = function(key, def) {
            if (key === "ro.debuggable") return "0";
            if (key === "ro.secure") return "1";
            if (key === "service.adb.root") return "0";
            return this.get(key, def);
        };
        SP.get.overload("java.lang.String").implementation = function(key) {
            if (key === "ro.debuggable") return "0";
            if (key === "ro.secure") return "1";
            if (key === "service.adb.root") return "0";
            return this.get(key);
        };
        try {
            SP.getBoolean.overload("java.lang.String", "boolean").implementation = function(key, def) {
                if (key === "ro.debuggable") return false;
                return this.getBoolean(key, def);
            };
        } catch(e2) {}
    } catch(e) {}

    // --- USB Manager (detect USB connected) ---
    try {
        var UsbManager = Java.use("android.hardware.usb.UsbManager");
        UsbManager.getAccessoryList.implementation = function () { return null; };
    } catch(e) {}

    // --- BatteryManager (USB charging state can reveal USB connection) ---
    try {
        var IntentFilter = Java.use("android.content.IntentFilter");
        var BatteryManager = Java.use("android.os.BatteryManager");
        var Intent = Java.use("android.content.Intent");
        Intent.getIntExtra.overload("java.lang.String", "int").implementation = function(name, def) {
            if (name === "plugged") {
                // Return AC charging (2) instead of USB (1)
                var val = this.getIntExtra(name, def);
                if (val === 2) return 1;  // USB → return AC
                return val;
            }
            return this.getIntExtra(name, def);
        };
    } catch(e) {}


    console.log("[+] USB/Developer bypass active (comprehensive)");

    // =============================================
    // DEBUG: Catch ExoPlayer / Player errors
    // =============================================
    try {
        // Hook Toast to catch any error messages shown
        var Toast = Java.use("android.widget.Toast");
        Toast.show.implementation = function() {
            try {
                // Get the toast text
                var view = this.getView();
                if (view) {
                    var tv = Java.cast(view, Java.use("android.view.ViewGroup")).getChildAt(0);
                    if (tv) {
                        var text = Java.cast(tv, Java.use("android.widget.TextView")).getText().toString();
                        console.log("[TOAST] " + text);
                    }
                }
            } catch(e) {
                console.log("[TOAST] (could not read text)");
            }
            return this.show();
        };
    } catch(e) {}

    try {
        // Hook AlertDialog to catch any popup error messages
        var AlertBuilder = Java.use("android.app.AlertDialog$Builder");
        AlertBuilder.setMessage.overload("java.lang.CharSequence").implementation = function(msg) {
            console.log("[ALERT] " + msg);
            return this.setMessage(msg);
        };
    } catch(e) {}

    // Log ALL exceptions thrown to find silent failures
    try {
        var Throwable = Java.use("java.lang.Throwable");
        Throwable.$init.overload("java.lang.String").implementation = function(msg) {
            if (msg && (
                msg.indexOf("drm") !== -1 || msg.indexOf("DRM") !== -1 ||
                msg.indexOf("player") !== -1 || msg.indexOf("Player") !== -1 ||
                msg.indexOf("Exo") !== -1 || msg.indexOf("exo") !== -1 ||
                msg.indexOf("video") !== -1 || msg.indexOf("Video") !== -1 ||
                msg.indexOf("developer") !== -1 || msg.indexOf("Developer") !== -1 ||
                msg.indexOf("debug") !== -1 || msg.indexOf("Debug") !== -1 ||
                msg.indexOf("USB") !== -1 || msg.indexOf("usb") !== -1 ||
                msg.indexOf("security") !== -1 || msg.indexOf("Security") !== -1 ||
                msg.indexOf("license") !== -1 || msg.indexOf("License") !== -1 ||
                msg.indexOf("blocked") !== -1 || msg.indexOf("denied") !== -1 ||
                msg.indexOf("not allowed") !== -1 || msg.indexOf("restrict") !== -1
            )) {
                console.log("[EXCEPTION] " + msg);
                console.log("[EXCEPTION-STACK] " + Java.use("android.util.Log").getStackTraceString(this));
            }
            return this.$init(msg);
        };
    } catch(e) {}

    // =============================================
    // PART 2: STORAGE
    // =============================================
    var capturedUrls = [];
    var capturedKeys = [];
    var pendingChallenge = null;   // base64 challenge from key_server
    var capturedLicenseResponse = null; // base64 license response for key_server

    function storeVideoUrl(url, source) {
        capturedUrls.push({ url: url, source: source, timestamp: new Date().toISOString() });
        console.log("[CAPTURED-URL #" + capturedUrls.length + "] " + source);
    }

    function bytesToHex(bytes) {
        var hex = "";
        for (var i = 0; i < bytes.length; i++) {
            var b = (bytes[i] & 0xFF).toString(16);
            hex += (b.length < 2 ? "0" : "") + b;
        }
        return hex;
    }

    function bytesToBase64(bytes) {
        return Java.use("android.util.Base64").encodeToString(bytes, 2).toString();
    }

    function base64ToBytes(b64str) {
        return Java.use("android.util.Base64").decode(b64str, 0);
    }

    // =============================================
    // PART 3: WIDEVINE DRM CHALLENGE SWAP + RESPONSE CAPTURE
    // =============================================

    // Hook MediaDrm.getKeyRequest - REPLACE challenge with ours if available
    try {
        var MediaDrm = Java.use("android.media.MediaDrm");

        // Try the 4-param overload first
        try {
            MediaDrm.getKeyRequest.overload("[B", "java.lang.String", "int", "java.util.HashMap").implementation = function (scope, mimeType, keyType, optParams) {
                console.log("\n[DRM] getKeyRequest(4-param) called");
                var result = this.getKeyRequest(scope, mimeType, keyType, optParams);
                if (pendingChallenge) {
                    console.log("[DRM] >>> SWAPPING challenge with ours! <<<");
                    var ourBytes = base64ToBytes(pendingChallenge);
                    // Create a new KeyRequest with our data
                    var KeyRequest = Java.use("android.media.MediaDrm$KeyRequest");
                    // Unfortunately KeyRequest constructor is not public, so we modify the data field
                    // We'll use reflection
                    var dataField = KeyRequest.class.getDeclaredField("mData");
                    dataField.setAccessible(true);
                    dataField.set(result, ourBytes);
                    console.log("[DRM]   Swapped: " + ourBytes.length + " bytes");
                    pendingChallenge = null;  // one-shot
                } else {
                    console.log("[DRM]   No pending challenge (normal flow)");
                }
                console.log("[DRM]   challenge size: " + result.getData().length + " bytes");
                return result;
            };
            console.log("[+] Hooked MediaDrm.getKeyRequest (4-param)");
        } catch (e) { console.log("[!] getKeyRequest 4-param: " + e); }

        // 5-param overload
        try {
            MediaDrm.getKeyRequest.overload("[B", "[B", "java.lang.String", "int", "java.util.HashMap").implementation = function (scope, init, mimeType, keyType, optParams) {
                console.log("\n[DRM] getKeyRequest(5-param) called");
                console.log("[DRM]   mimeType: " + mimeType + " keyType: " + keyType);
                
                // Capture the PSSH from init data
                if (init && init.length > 0) {
                    var psshB64 = bytesToBase64(init);
                    console.log("[DRM]   init/PSSH: " + psshB64.substring(0, 60) + "...");
                }

                var result = this.getKeyRequest(scope, init, mimeType, keyType, optParams);
                
                if (pendingChallenge) {
                    console.log("[DRM] >>> SWAPPING challenge with ours! <<<");
                    var ourBytes = base64ToBytes(pendingChallenge);
                    try {
                        var KeyRequest = Java.use("android.media.MediaDrm$KeyRequest");
                        var dataField = KeyRequest.class.getDeclaredField("mData");
                        dataField.setAccessible(true);
                        dataField.set(result, ourBytes);
                        console.log("[DRM]   Swapped: " + ourBytes.length + " bytes");
                    } catch(e2) {
                        console.log("[DRM]   Swap via reflection failed: " + e2);
                    }
                    pendingChallenge = null;
                }
                
                console.log("[DRM]   final challenge size: " + result.getData().length + " bytes");
                console.log("[DRM]   license URL: " + result.getDefaultUrl());
                return result;
            };
            console.log("[+] Hooked MediaDrm.getKeyRequest (5-param)");
        } catch (e) { console.log("[!] getKeyRequest 5-param: " + e); }
        
    } catch (e) {
        console.log("[!] MediaDrm.getKeyRequest hook failed: " + e);
    }

    // Hook MediaDrm.provideKeyResponse - CAPTURE the license response
    try {
        var MediaDrm2 = Java.use("android.media.MediaDrm");
        MediaDrm2.provideKeyResponse.implementation = function (scope, response) {
            console.log("\n[DRM-KEY] provideKeyResponse called!");
            console.log("[DRM-KEY]   sessionId: " + bytesToHex(scope));
            console.log("[DRM-KEY]   response size: " + response.length + " bytes");
            
            // Store the license response as base64 for key_server
            capturedLicenseResponse = bytesToBase64(response);
            console.log("[DRM-KEY]   License response captured! (" + capturedLicenseResponse.length + " chars b64)");

            var result = this.provideKeyResponse(scope, response);
            console.log("[DRM-KEY]   keySetId: " + (result ? bytesToHex(result) : "null"));
            return result;
        };
        console.log("[+] Hooked MediaDrm.provideKeyResponse");
    } catch (e) {
        console.log("[!] MediaDrm.provideKeyResponse hook failed: " + e);
    }

    // Hook MediaCodec for key ID capture
    try {
        var MediaCodec = Java.use("android.media.MediaCodec");
        MediaCodec.queueSecureInputBuffer.implementation = function (index, offset, info, presentationTimeUs, flags) {
            if (!this._logged) {
                this._logged = true;
                console.log("\n[DECRYPT] MediaCodec.queueSecureInputBuffer called");
                var keyVal = info.key.value;
                var ivVal = info.iv.value;
                if (keyVal) console.log("[DECRYPT]   KID: " + bytesToHex(keyVal));
                if (ivVal) console.log("[DECRYPT]   IV:  " + bytesToHex(ivVal));
                capturedKeys.push({
                    key: keyVal ? bytesToHex(keyVal) : null,
                    iv: ivVal ? bytesToHex(ivVal) : null,
                    timestamp: new Date().toISOString()
                });
            }
            return this.queueSecureInputBuffer(index, offset, info, presentationTimeUs, flags);
        };
        console.log("[+] Hooked MediaCodec.queueSecureInputBuffer");
    } catch (e) { console.log("[!] MediaCodec hook failed: " + e); }

    // =============================================
    // PART 4: URL CAPTURE HOOKS
    // =============================================
    try {
        var n0 = Java.use("oa.n0");
        n0.Jb.implementation = function (a, b, c, d) {
            console.log("\n========= oa.n0.Jb() CALLED =========");
            console.log("[PARAM-1 videoUrl] " + a);
            console.log("[PARAM-2 encKey]   " + b);
            console.log("======================================\n");
            storeVideoUrl(a, "oa.n0.Jb");
            return this.Jb(a, b, c, d);
        };
        console.log("[+] Hooked oa.n0.Jb");
    } catch (e) { console.log("[!] oa.n0.Jb hook failed: " + e); }

    try {
        var URL = Java.use("java.net.URL");
        URL.openConnection.overload().implementation = function () {
            var urlStr = this.toString();
            if (urlStr.indexOf(".m3u8") !== -1 || urlStr.indexOf(".mpd") !== -1 || 
                urlStr.indexOf("license") !== -1 || urlStr.indexOf("widevine") !== -1 ||
                urlStr.indexOf("getlicense") !== -1 || urlStr.indexOf("drm") !== -1) {
                console.log("\n[URL-CONN] " + urlStr + "\n");
                storeVideoUrl(urlStr, "url_openConnection");
            }
            return this.openConnection();
        };
        console.log("[+] Hooked URL.openConnection()");
    } catch (e) { console.log("[i] URL.openConnection hook failed"); }

    // =============================================
    // PART 5: HTTP SERVER ON PORT 8899
    // =============================================
    function startServer() {
        var JThread = Java.use("java.lang.Thread");
        var JRunnable = Java.use("java.lang.Runnable");
        var serverRunnable = Java.registerClass({
            name: "com.frida.bridge.ServerMain",
            implements: [JRunnable],
            methods: {
                run: function () {
                    var SS = Java.use("java.net.ServerSocket");
                    var server = SS.$new(8899);
                    server.setReuseAddress(true);
                    console.log("[+] HTTP server listening on port 8899");
                    while (true) {
                        try {
                            var clientSocket = server.accept();
                            var handler = Java.registerClass({
                                name: "com.frida.bridge.H" + Date.now() + Math.random().toString().substring(2, 6),
                                implements: [JRunnable],
                                fields: { sock: "java.net.Socket" },
                                methods: { run: function () { handleRequest(this.sock.value); } }
                            });
                            var h = handler.$new();
                            h.sock.value = clientSocket;
                            JThread.$new(Java.cast(h, JRunnable)).start();
                        } catch (e) { console.log("[!] Accept error: " + e); }
                    }
                }
            }
        });
        JThread.$new(Java.cast(serverRunnable.$new(), JRunnable)).start();
    }

    function readBody(is, contentLength) {
        var buf = [];
        for (var i = 0; i < contentLength; i++) {
            var b = is.read();
            if (b === -1) break;
            buf.push(b & 0xFF);
        }
        return String.fromCharCode.apply(null, buf);
    }

    function handleRequest(sock) {
        try {
            var is = sock.getInputStream();
            var lineBuffer = [], requestLine = "", lineCount = 0, headers = {};
            while (true) {
                var b = is.read();
                if (b === -1) break;
                if (b === 13) continue;
                if (b === 10) {
                    var line = String.fromCharCode.apply(null, lineBuffer);
                    lineBuffer = [];
                    if (lineCount === 0) requestLine = line;
                    else {
                        var colonIdx = line.indexOf(":");
                        if (colonIdx > 0) {
                            headers[line.substring(0, colonIdx).toLowerCase().trim()] = line.substring(colonIdx + 1).trim();
                        }
                    }
                    lineCount++;
                    if (line.length === 0) break;
                    continue;
                }
                lineBuffer.push(b);
            }
            if (!requestLine) { sock.close(); return; }
            var parts = requestLine.split(" ");
            var method = parts[0];
            var fullPath = parts.length > 1 ? parts[1] : "/";
            var qIdx = fullPath.indexOf("?");
            var path = qIdx >= 0 ? fullPath.substring(0, qIdx) : fullPath;
            var queryStr = qIdx >= 0 ? fullPath.substring(qIdx + 1) : "";
            var params = {};
            if (queryStr.length > 0) {
                var pairs = queryStr.split("&");
                for (var i = 0; i < pairs.length; i++) {
                    var eqIdx = pairs[i].indexOf("=");
                    if (eqIdx > 0) params[pairs[i].substring(0, eqIdx)] = decodeURIComponent(pairs[i].substring(eqIdx + 1));
                }
            }

            // Read POST body if present
            var body = "";
            var cl = parseInt(headers["content-length"] || "0");
            if (cl > 0) body = readBody(is, cl);

            var responseJson;
            if (path === "/health") responseJson = '{"status":"ok"}';
            else if (path === "/token") responseJson = getTokenJson();
            else if (path === "/video-url") {
                if (!params["id"]) responseJson = '{"error":"id param required"}';
                else responseJson = getVideoUrlJson(params["id"]);
            }
            else if (path === "/captured-urls") responseJson = JSON.stringify({ count: capturedUrls.length, urls: capturedUrls });
            else if (path === "/keys") responseJson = JSON.stringify({ count: capturedKeys.length, keys: capturedKeys });
            else if (path === "/latest") responseJson = capturedUrls.length > 0 ? JSON.stringify(capturedUrls[capturedUrls.length - 1]) : '{"error":"no urls yet"}';
            else if (path === "/clear") { capturedUrls = []; capturedKeys = []; capturedLicenseResponse = null; responseJson = '{"cleared":true}'; }
            
            // NEW: Set pending challenge (from key_server)
            else if (path === "/set-challenge" && method === "POST") {
                try {
                    var data = JSON.parse(body);
                    pendingChallenge = data.challenge;  // base64 encoded challenge
                    console.log("[BRIDGE] Challenge set! (" + (pendingChallenge ? pendingChallenge.length : 0) + " chars)");
                    responseJson = '{"status":"challenge_set","size":' + (pendingChallenge ? pendingChallenge.length : 0) + '}';
                } catch (e) {
                    responseJson = '{"error":"' + e.toString().replace(/"/g, '\\"') + '"}';
                }
            }
            // NEW: Get captured license response
            else if (path === "/license-response") {
                if (capturedLicenseResponse) {
                    responseJson = JSON.stringify({ 
                        status: "captured", 
                        license: capturedLicenseResponse,
                        size: capturedLicenseResponse.length
                    });
                } else {
                    responseJson = '{"status":"waiting","license":null}';
                }
            }
            // NEW: Clear license response
            else if (path === "/clear-license") {
                capturedLicenseResponse = null;
                responseJson = '{"cleared":true}';
            }

            else responseJson = '{"endpoints":["/health","/token","/video-url?id=X","/captured-urls","/keys","/latest","/clear","/set-challenge","/license-response","/clear-license"]}';

            var httpResponse = "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nAccess-Control-Allow-Origin: *\r\nConnection: close\r\nContent-Length: " + responseJson.length + "\r\n\r\n" + responseJson;
            var os = sock.getOutputStream();
            os.write(Java.use("java.lang.String").$new(httpResponse).getBytes("UTF-8"));
            os.flush();
            sock.close();
        } catch (e) {
            console.log("[!] Handler error: " + e);
            try { sock.close(); } catch (x) {}
        }
    }

    // =============================================
    // PART 6: CRYPTO + API HELPERS
    // =============================================
    function getTokenJson() {
        try {
            var app = Java.use("co.classplus.app.ClassplusApplication").x();
            return '{"success":true,"token":"' + app.l().getAccessToken().toString() + '"}';
        } catch (e) { return '{"success":false,"error":"' + escJson(e.toString()) + '"}'; }
    }
    function getVideoUrlJson(contentId) {
        try {
            var app = Java.use("co.classplus.app.ClassplusApplication").x();
            var token = app.l().getAccessToken().toString();
            var encId = encryptContentId(contentId.toString());
            var url = "https://api.classplusapp.com/cams/uploader/video/jw-signed-url?contentId=" + Java.use("java.net.URLEncoder").encode(encId, "UTF-8").toString() + "&offlineDownload=false";
            var builder = Java.use("okhttp3.Request$Builder").$new();
            builder.url(url);
            builder.addHeader("x-access-token", token);
            builder.addHeader("User-Agent", "Mobile-Android");
            builder.addHeader("App-Version", "1.12.1.2");
            builder.addHeader("Api-Version", "56");
            builder.addHeader("Device-Id", "4b7ed5ff3b8cadf8");
            builder.addHeader("Device-Details", "samsung_SM-M346B_SDK-36");
            builder.addHeader("region", "IN");
            builder.addHeader("Content-Type", "application/json");
            builder.addHeader("Build-Number", "56");
            builder.addHeader("is-apk", "0");
            var client = Java.use("okhttp3.OkHttpClient").$new();
            var resp = client.newCall(builder.build()).execute();
            var body = resp.body().string().toString();
            return body;
        } catch (e) {
            return '{"error":"' + escJson(e.toString()) + '"}';
        }
    }
    function escJson(s) { return s.replace(/\\/g, "\\\\").replace(/"/g, '\\"').replace(/\n/g, " ").replace(/\r/g, ""); }
    function encryptContentId(id) {
        try {
            var password = Java.use("co.classplus.app.ClassplusApplication").getMagic();
            var salt = Java.array("byte", [0, 0, 0, 0, 0, 0, 0, 0]);
            Java.use("java.security.SecureRandom").$new().nextBytes(salt);
            var passBytes = Java.use("java.lang.String").$new(password).getBytes("UTF-8");
            var md5 = Java.use("java.security.MessageDigest").getInstance("MD5");
            md5.update(passBytes); md5.update(salt); var d1 = md5.digest();
            md5.reset(); md5.update(d1); md5.update(passBytes); md5.update(salt); var d2 = md5.digest();
            md5.reset(); md5.update(d2); md5.update(passBytes); md5.update(salt); var d3 = md5.digest();
            var keyBytes = Java.array("byte", new Array(32).fill(0));
            var ivBytes = Java.array("byte", new Array(16).fill(0));
            for (var i = 0; i < 16; i++) { keyBytes[i] = d1[i]; keyBytes[16 + i] = d2[i]; ivBytes[i] = d3[i]; }
            var cipher = Java.use("javax.crypto.Cipher").getInstance("AES/CBC/PKCS5Padding");
            cipher.init(1, Java.use("javax.crypto.spec.SecretKeySpec").$new(keyBytes, "AES"), Java.use("javax.crypto.spec.IvParameterSpec").$new(ivBytes));
            var encrypted = cipher.doFinal(Java.use("java.lang.String").$new(id).getBytes("UTF-8"));
            var prefix = Java.use("java.lang.String").$new("Salted__").getBytes("US-ASCII");
            var result = Java.array("byte", new Array(16 + encrypted.length).fill(0));
            for (var i = 0; i < 8; i++) result[i] = prefix[i];
            for (var i = 0; i < 8; i++) result[8 + i] = salt[i];
            for (var i = 0; i < encrypted.length; i++) result[16 + i] = encrypted[i];
            return Java.use("android.util.Base64").encodeToString(result, 2).toString();
        } catch (e) { console.log("[!] encrypt error: " + e); return ""; }
    }

    // =============================================
    // START
    // =============================================
    startServer();
    console.log("[+] Bridge ready! Endpoints:");
    console.log("    /captured-urls, /keys, /latest, /clear");
    console.log("    /set-challenge (POST), /license-response, /clear-license");
    console.log("[+] DRM hooks active with challenge swap support!");
});
