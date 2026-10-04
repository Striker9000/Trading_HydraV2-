// Test: node test-crosscheck.mjs core.js <BLOCK_PSK> "<cookout phrase>" "<kitchen phrase>" 2026-11
// Output must equal: python3 derive_keys.py --cookout ... --kitchen ... --month 2026-11
import fs from "fs";
eval(fs.readFileSync(process.argv[2], "utf8"));
const K = globalThis.BleshKeys;
const block = Uint8Array.from(Buffer.from(process.argv[3], "base64"));
const [cp, kp, month] = process.argv.slice(4);
const ck = await K.deriveKey(cp, "cookout", month), kk = await K.deriveKey(kp, "kitchen", month);
console.log(`Month ${month}\nCookout check code: ${await K.checkCode(ck)}\nKitchen check code: ${await K.checkCode(kk)}`);
console.log(K.setupURL([{name:"",psk:new Uint8Array([1])},{name:"Bleshtastic",psk:block},{name:"Cookout",psk:ck},{name:"Kitchen",psk:kk}], false));
