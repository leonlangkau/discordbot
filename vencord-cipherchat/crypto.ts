/*
 * CipherChat — client-side message encryption for Vencord
 *
 * AES-256-GCM with PBKDF2-SHA256 key derivation, built entirely on the
 * WebCrypto API that ships with Electron. Nothing leaves the client
 * unencrypted; Discord only ever sees the base64url blob.
 */

const te = new TextEncoder();
const td = new TextDecoder();

/** Format version. Bumped if the wire format ever changes. */
export const VERSION = 1;

/** Domain separator so a passphrase used here can't collide with another app. */
const SALT_CONTEXT = "cipherchat/v1/salt|";
const AAD = te.encode("cipherchat/v1");

const PBKDF2_ITERATIONS = 210_000;
const IV_BYTES = 12;

/**
 * Wire format: 🔒cc1:<base64url payload>🔒
 * payload = version(1) || iv(12) || ciphertext+tag
 */
const OPEN = "\u{1F512}cc1:";
const CLOSE = "\u{1F512}";

export const CIPHERTEXT_RE = /\u{1F512}cc1:([A-Za-z0-9_-]+)\u{1F512}/gu;

export function wrap(payload: string) {
    return OPEN + payload + CLOSE;
}

export function containsCiphertext(content: string) {
    CIPHERTEXT_RE.lastIndex = 0;
    return CIPHERTEXT_RE.test(content);
}

export function extractCiphertexts(content: string) {
    CIPHERTEXT_RE.lastIndex = 0;
    return [...content.matchAll(CIPHERTEXT_RE)].map(m => m[1]);
}

// ── base64url ────────────────────────────────────────────────────────────────

function toBase64Url(bytes: Uint8Array) {
    let bin = "";
    for (const b of bytes) bin += String.fromCharCode(b);
    return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function fromBase64Url(s: string) {
    const b64 = s.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(s.length / 4) * 4, "=");
    const bin = atob(b64);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
}

// ── key derivation ───────────────────────────────────────────────────────────

const keyCache = new Map<string, Promise<CryptoKey>>();

/**
 * The salt is derived deterministically from the passphrase so that two
 * clients who share only the passphrase land on the same key — that is the
 * whole point of a shared secret. The tradeoff is that a weak passphrase is
 * vulnerable to precomputation, hence the high iteration count and the advice
 * in the README to use a long random key.
 */
export async function deriveKey(passphrase: string): Promise<CryptoKey> {
    const cached = keyCache.get(passphrase);
    if (cached) return cached;

    const pending = (async () => {
        const salt = new Uint8Array(
            await crypto.subtle.digest("SHA-256", te.encode(SALT_CONTEXT + passphrase))
        );
        const material = await crypto.subtle.importKey(
            "raw", te.encode(passphrase), "PBKDF2", false, ["deriveKey"]
        );
        return crypto.subtle.deriveKey(
            { name: "PBKDF2", salt, iterations: PBKDF2_ITERATIONS, hash: "SHA-256" },
            material,
            { name: "AES-GCM", length: 256 },
            false,
            ["encrypt", "decrypt"]
        );
    })();

    keyCache.set(passphrase, pending);
    // Don't cache a rejected derivation.
    pending.catch(() => keyCache.delete(passphrase));
    return pending;
}

export function clearKeyCache() {
    keyCache.clear();
}

/** Short, non-reversible tag for a key, so users can eyeball that two clients match. */
export async function keyFingerprint(passphrase: string) {
    const digest = new Uint8Array(
        await crypto.subtle.digest("SHA-256", te.encode("cipherchat/v1/fp|" + passphrase))
    );
    return toBase64Url(digest.slice(0, 4));
}

// ── encrypt / decrypt ────────────────────────────────────────────────────────

export async function encrypt(plaintext: string, passphrase: string): Promise<string> {
    const key = await deriveKey(passphrase);
    const iv = crypto.getRandomValues(new Uint8Array(IV_BYTES));
    const ct = new Uint8Array(
        await crypto.subtle.encrypt(
            { name: "AES-GCM", iv, additionalData: AAD },
            key,
            te.encode(plaintext)
        )
    );

    const payload = new Uint8Array(1 + iv.length + ct.length);
    payload[0] = VERSION;
    payload.set(iv, 1);
    payload.set(ct, 1 + iv.length);

    return toBase64Url(payload);
}

export class DecryptError extends Error {}

export async function decrypt(payload: string, passphrase: string): Promise<string> {
    let bytes: Uint8Array;
    try {
        bytes = fromBase64Url(payload);
    } catch {
        throw new DecryptError("Malformed payload");
    }

    if (bytes.length < 1 + IV_BYTES + 16) throw new DecryptError("Payload too short");
    if (bytes[0] !== VERSION) throw new DecryptError(`Unsupported format version ${bytes[0]}`);

    const key = await deriveKey(passphrase);
    const iv = bytes.slice(1, 1 + IV_BYTES);
    const ct = bytes.slice(1 + IV_BYTES);

    try {
        const pt = await crypto.subtle.decrypt(
            { name: "AES-GCM", iv, additionalData: AAD },
            key,
            ct
        );
        return td.decode(pt);
    } catch {
        // GCM auth failure — wrong key, or the message was tampered with.
        throw new DecryptError("Wrong key");
    }
}

/** Try a list of passphrases, returning the first that authenticates. */
export async function decryptWithAny(
    payload: string,
    passphrases: string[]
): Promise<{ text: string; passphrase: string; }> {
    let lastError: unknown;
    for (const passphrase of passphrases) {
        if (!passphrase) continue;
        try {
            return { text: await decrypt(payload, passphrase), passphrase };
        } catch (e) {
            lastError = e;
        }
    }
    throw lastError instanceof Error ? lastError : new DecryptError("No key available");
}

/** Cryptographically random passphrase for `/cipherchat genkey`. */
export function generateKey(bytes = 32) {
    return toBase64Url(crypto.getRandomValues(new Uint8Array(bytes)));
}
