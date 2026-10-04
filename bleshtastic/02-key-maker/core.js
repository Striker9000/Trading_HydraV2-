// Bleshtastic key maker core. Runs in the browser and in Node 20+ (WebCrypto).
// A phrase + a tier + a month -> a 256-bit Meshtastic channel key -> a setup link.
// Spec (v1), mirrored in derive_keys.py:
//   phrase  = NFKC, lowercase, keep only letters/digits/spaces, collapse spaces, trim
//   salt    = "bleshtastic/v1/<tier>/<YYYY-MM>"
//   key     = PBKDF2-HMAC-SHA256(phrase, salt, 600000 iterations, 32 bytes)
//   check   = first 3 bytes of SHA-256(key), hex, "7F3-A21" style (safe to say out loud)
(function (root) {
  const ITER = 600000;
  const enc = new TextEncoder();
  const subtle = (root.crypto || globalThis.crypto).subtle;

  function normalize(p) {
    return p.normalize("NFKC").toLowerCase()
      .replace(/[^\p{L}\p{N}\s]/gu, "")
      .replace(/\s+/g, " ").trim();
  }

  async function deriveKey(phrase, tier, month) {
    const base = await subtle.importKey("raw", enc.encode(normalize(phrase)), "PBKDF2", false, ["deriveBits"]);
    const bits = await subtle.deriveBits(
      { name: "PBKDF2", hash: "SHA-256", salt: enc.encode(`bleshtastic/v1/${tier}/${month}`), iterations: ITER },
      base, 256);
    return new Uint8Array(bits);
  }

  async function checkCode(key) {
    const h = new Uint8Array(await subtle.digest("SHA-256", key));
    const hex = Array.from(h.slice(0, 3), b => b.toString(16).padStart(2, "0")).join("").toUpperCase();
    return hex.slice(0, 3) + "-" + hex.slice(3);
  }

  // --- minimal protobuf writer (only what a ChannelSet needs) ---
  function varint(n) { const o = []; while (n > 127) { o.push((n & 127) | 128); n >>>= 7; } o.push(n); return o; }
  const tag = (f, w) => varint((f << 3) | w);
  const vfield = (f, v) => [...tag(f, 0), ...varint(v)];
  const lfield = (f, bytes) => [...tag(f, 2), ...varint(bytes.length), ...bytes];

  function channelSettings(name, psk) {
    let b = lfield(2, Array.from(psk));
    if (!name) return b;                      // default LongFast: key only, like the stock config
    b = b.concat(lfield(3, Array.from(enc.encode(name))));
    return b.concat(lfield(7, []));          // module_settings{position_precision:0} = location off
  }
  function loraConfig() {                     // US, LONG_FAST preset, 3 hops, transmit on
    return [...vfield(1, 1), ...vfield(7, 1), ...vfield(8, 3), ...vfield(9, 1)];
  }
  function b64url(bytes) {
    let s = ""; bytes.forEach(b => s += String.fromCharCode(b));
    return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }
  const b64 = bytes => { let s = ""; bytes.forEach(b => s += String.fromCharCode(b)); return btoa(s); };

  // channels: [{name, psk:Uint8Array}] in slot order. add=true keeps what's already on the radio.
  function setupURL(channels, add) {
    let body = [];
    channels.forEach(c => body = body.concat(lfield(1, channelSettings(c.name, c.psk))));
    body = body.concat(lfield(2, loraConfig()));
    return `https://meshtastic.org/e/${add ? "?add=true" : ""}#${b64url(body)}`;
  }

  function monthKey(d) { return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`; }

  root.BleshKeys = { ITER, normalize, deriveKey, checkCode, setupURL, monthKey, b64 };
})(typeof window !== "undefined" ? window : globalThis);
