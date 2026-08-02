/*
 * Session-only memo of decryption results, keyed by channel + payloads.
 *
 * Messages re-render constantly and decryption is async, so results are cached
 * to avoid re-deriving keys on every paint. Nothing here is persisted; it lives
 * in this dependency-free module so both the settings schema and the renderer
 * can invalidate it without an import cycle.
 */

export type Decrypted = {
    ok: true;
    lines: string[];
    fingerprint: string;
} | {
    ok: false;
    reason: string;
};

const cache = new Map<string, Decrypted>();

/**
 * Bumped whenever the known keys change, so already-mounted messages re-attempt
 * decryption on their next render instead of showing a stale "wrong key"
 * result forever.
 */
let epoch = 0;

export function cacheKey(channelId: string, payloads: string[]) {
    return `${epoch}|${channelId}|${payloads.join(",")}`;
}

export function getCached(key: string) {
    return cache.get(key);
}

export function setCached(key: string, value: Decrypted) {
    cache.set(key, value);
}

export function clearDecryptCache() {
    cache.clear();
    epoch++;
}
