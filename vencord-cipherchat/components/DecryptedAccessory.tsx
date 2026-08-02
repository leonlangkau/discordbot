import { Text, useEffect, useState } from "@webpack/common";
import type { Message } from "discord-types/general";

import { decryptWithAny, extractCiphertexts, keyFingerprint } from "../crypto";
import { cacheKey, getCached, setCached } from "../decryptcache";
import type { Decrypted } from "../decryptcache";
import { allKnownKeys } from "../keystore";
import { settings } from "../settings";

async function decryptAll(channelId: string, payloads: string[]): Promise<Decrypted> {
    const keys = allKnownKeys(channelId);
    if (!keys.length) return { ok: false, reason: "No key configured — set one with /cipherchat setkey" };

    const lines: string[] = [];
    let used = keys[0];

    for (const payload of payloads) {
        try {
            const { text, passphrase } = await decryptWithAny(payload, keys);
            lines.push(text);
            used = passphrase;
        } catch (e) {
            return { ok: false, reason: e instanceof Error ? e.message : "Could not decrypt" };
        }
    }

    return { ok: true, lines, fingerprint: await keyFingerprint(used) };
}

export function DecryptedAccessory({ message }: { message: Message; }) {
    const payloads = extractCiphertexts(message.content ?? "");
    const key = cacheKey(message.channel_id, payloads);
    const [state, setState] = useState<Decrypted | undefined>(() => getCached(key));

    useEffect(() => {
        if (!payloads.length) return;

        const cached = getCached(key);
        if (cached) {
            setState(cached);
            return;
        }

        let live = true;
        decryptAll(message.channel_id, payloads).then(result => {
            setCached(key, result);
            if (live) setState(result);
        });
        return () => { live = false; };
    }, [key]);

    if (!payloads.length) return null;

    if (!state) {
        return (
            <Text variant="text-sm/normal" style={{ opacity: 0.6 }}>
                🔒 Decrypting…
            </Text>
        );
    }

    if (!state.ok) {
        if (!settings.store.markFailures) return null;
        return (
            <Text variant="text-sm/normal" style={{ color: "var(--text-danger)" }}>
                🔒 {state.reason}
            </Text>
        );
    }

    return (
        <div style={{
            borderLeft: "3px solid var(--brand-experiment)",
            paddingLeft: "8px",
            margin: "2px 0"
        }}>
            {state.lines.map((line, i) => (
                <Text key={i} variant="text-md/normal" style={{ whiteSpace: "pre-wrap" }}>
                    {line}
                </Text>
            ))}
            {settings.store.showFingerprint && (
                <Text variant="text-xs/normal" style={{ opacity: 0.5 }}>
                    🔓 key {state.fingerprint}
                </Text>
            )}
        </div>
    );
}
