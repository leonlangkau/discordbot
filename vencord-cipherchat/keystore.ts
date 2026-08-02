/*
 * Per-channel key + toggle bookkeeping.
 *
 * Vencord settings only hold primitives, so the maps are persisted as JSON
 * strings and mirrored in memory for cheap synchronous reads during render.
 */

import { clearKeyCache } from "./crypto";
import { clearDecryptCache } from "./decryptcache";
import { settings } from "./settings";

function readJson<T>(raw: string, fallback: T): T {
    try {
        const parsed = JSON.parse(raw);
        return parsed ?? fallback;
    } catch {
        return fallback;
    }
}

// ── per-channel keys ─────────────────────────────────────────────────────────

export function getChannelKeys(): Record<string, string> {
    return readJson<Record<string, string>>(settings.store.channelKeys, {});
}

export function getChannelKey(channelId: string): string | undefined {
    return getChannelKeys()[channelId];
}

export function setChannelKey(channelId: string, key: string | null) {
    const keys = getChannelKeys();
    if (key) keys[channelId] = key;
    else delete keys[channelId];
    settings.store.channelKeys = JSON.stringify(keys);
    clearKeyCache();
    clearDecryptCache();
}

/** The key a message in this channel should be encrypted with, if any. */
export function keyForChannel(channelId: string): string | undefined {
    return getChannelKey(channelId) || settings.store.defaultKey || undefined;
}

/**
 * Every key we know about, channel-specific first. Incoming messages are tried
 * against all of them, so history stays readable after a key rotation.
 */
export function allKnownKeys(channelId: string): string[] {
    const keys: string[] = [];
    const channelKey = getChannelKey(channelId);
    if (channelKey) keys.push(channelKey);
    if (settings.store.defaultKey) keys.push(settings.store.defaultKey);
    for (const key of Object.values(getChannelKeys())) {
        if (!keys.includes(key)) keys.push(key);
    }
    return keys;
}

// ── per-channel enable toggle ────────────────────────────────────────────────

export function isEnabled(channelId: string): boolean {
    if (settings.store.encryptByDefault) return true;
    return readJson<string[]>(settings.store.enabledChannels, []).includes(channelId);
}

export function setEnabled(channelId: string, enabled: boolean) {
    const list = readJson<string[]>(settings.store.enabledChannels, []).filter(id => id !== channelId);
    if (enabled) list.push(channelId);
    settings.store.enabledChannels = JSON.stringify(list);
}

export function toggleEnabled(channelId: string): boolean {
    const next = !isEnabled(channelId);
    setEnabled(channelId, next);
    return next;
}
