/*
 * CipherChat — end-to-end encrypted Discord messages, entirely client side.
 *
 * Outgoing messages are encrypted with AES-256-GCM before they ever reach
 * Discord's API; the server only stores an opaque blob. Any other client
 * running this plugin with the same key renders the plaintext underneath the
 * message.
 */

import {
    ApplicationCommandInputType,
    ApplicationCommandOptionType,
    findOption,
    sendBotMessage
} from "@api/Commands";
import { addMessagePreSendListener, removeMessagePreSendListener } from "@api/MessageEvents";
import { ChatBarButton, ChatBarButtonFactory } from "@api/ChatButtons";
import definePlugin from "@utils/types";
import { React, Toasts } from "@webpack/common";

import { DecryptedAccessory } from "./components/DecryptedAccessory";
import { clearKeyCache, containsCiphertext, encrypt, generateKey, keyFingerprint, wrap } from "./crypto";
import { clearDecryptCache } from "./decryptcache";
import { isEnabled, keyForChannel, setChannelKey, toggleEnabled } from "./keystore";
import { settings } from "./settings";

/** Discord's limit for non-Nitro accounts; Nitro users can raise this safely. */
const MAX_MESSAGE_LENGTH = 2000;

let preSendListener: ReturnType<typeof addMessagePreSendListener> | undefined;

function toast(message: string, type: number) {
    Toasts.show({ message, id: Toasts.genId(), type });
}

function LockIcon({ locked }: { locked: boolean; }) {
    return (
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path
                fill="currentColor"
                d="M12 2a5 5 0 0 0-5 5v3H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8a2 2 0 0 0-2-2h-1V7a5 5 0 0 0-5-5Zm3 8H9V7a3 3 0 1 1 6 0v3Z"
                opacity={locked ? 1 : 0.5}
            />
            {!locked && (
                <path stroke="currentColor" strokeWidth="2" strokeLinecap="round" d="M4 20 20 4" />
            )}
        </svg>
    );
}

const CipherChatButton: ChatBarButtonFactory = ({ isMainChat, channel }) => {
    // Hook first: bailing out before it would break the rules of hooks.
    // Re-renders this button when the channel's state flips.
    const [, forceUpdate] = React.useReducer(x => x + 1, 0);

    if (!isMainChat) return null;

    const enabled = isEnabled(channel.id);
    const hasKey = !!keyForChannel(channel.id);

    return (
        <ChatBarButton
            tooltip={
                enabled
                    ? hasKey ? "CipherChat: encrypting (click to disable)" : "CipherChat: on, but no key set — use /cipherchat setkey"
                    : "CipherChat: off (click to encrypt this channel)"
            }
            onClick={() => {
                const next = toggleEnabled(channel.id);
                forceUpdate();
                if (next && !keyForChannel(channel.id)) {
                    toast("CipherChat: no key for this channel. Use /cipherchat setkey.", Toasts.Type.FAILURE);
                }
            }}
        >
            <LockIcon locked={enabled && hasKey} />
        </ChatBarButton>
    );
};

export default definePlugin({
    name: "CipherChat",
    description:
        "Encrypts your messages client-side with AES-256-GCM before sending. Recipients running CipherChat with the same key see the plaintext; everyone else (and Discord) sees a garbled blob.",
    authors: [{ name: "CipherChat", id: 0n }],
    dependencies: ["MessageEventsAPI", "ChatInputButtonAPI", "MessageAccessoriesAPI", "CommandsAPI"],
    settings,

    renderChatBarButton: CipherChatButton,

    renderMessageAccessory: (props: any) => <DecryptedAccessory message={props.message} />,

    commands: [
        {
            name: "cipherchat",
            description: "Manage CipherChat encryption keys for this channel",
            inputType: ApplicationCommandInputType.BUILT_IN,
            options: [
                {
                    name: "action",
                    description: "What to do",
                    type: ApplicationCommandOptionType.STRING,
                    required: true,
                    choices: [
                        { name: "setkey — use a key in this channel", value: "setkey", label: "setkey" },
                        { name: "genkey — generate a random key and use it here", value: "genkey", label: "genkey" },
                        { name: "clearkey — forget this channel's key", value: "clearkey", label: "clearkey" },
                        { name: "toggle — turn encryption on/off here", value: "toggle", label: "toggle" },
                        { name: "status — show current key and state", value: "status", label: "status" }
                    ]
                },
                {
                    name: "key",
                    description: "The shared passphrase (for setkey)",
                    type: ApplicationCommandOptionType.STRING,
                    required: false
                }
            ],
            execute: async (opts, ctx) => {
                const action = findOption<string>(opts, "action", "status");
                const channelId = ctx.channel.id;
                // Clipped to a void return: anything `execute` returns with a
                // `content` field would be sent to the channel for real.
                const reply = (content: string): void => {
                    sendBotMessage(channelId, { content });
                };

                switch (action) {
                    case "setkey": {
                        const key = findOption<string>(opts, "key", "");
                        if (!key) return reply("You need to pass a `key` for `setkey`.");
                        setChannelKey(channelId, key);
                        return reply(
                            `🔒 Key set for this channel (fingerprint \`${await keyFingerprint(key)}\`). ` +
                            "Share the exact same key with the other side, over something that isn't Discord."
                        );
                    }
                    case "genkey": {
                        const key = generateKey();
                        setChannelKey(channelId, key);
                        return reply(
                            `🔒 Generated a key for this channel (fingerprint \`${await keyFingerprint(key)}\`):\n` +
                            `\`\`\`\n${key}\n\`\`\`\n` +
                            "Send this to the other side through a channel you trust — **not** here."
                        );
                    }
                    case "clearkey": {
                        setChannelKey(channelId, null);
                        return reply("🔓 Channel key cleared. Falling back to the default key, if one is set.");
                    }
                    case "toggle": {
                        const next = toggleEnabled(channelId);
                        return reply(`CipherChat is now **${next ? "on" : "off"}** for this channel.`);
                    }
                    default: {
                        const key = keyForChannel(channelId);
                        return reply(
                            `**CipherChat status**\n` +
                            `• Encryption: ${isEnabled(channelId) ? "on" : "off"}\n` +
                            `• Key: ${key ? `set (fingerprint \`${await keyFingerprint(key)}\`)` : "**none**"}\n` +
                            `• Source: ${key ? (key === settings.store.defaultKey ? "default key" : "channel key") : "—"}`
                        );
                    }
                }
            }
        }
    ],

    start() {
        preSendListener = addMessagePreSendListener(async (channelId, msg) => {
            if (!msg.content || !isEnabled(channelId)) return;
            // Don't re-encrypt something that is already a CipherChat payload.
            if (containsCiphertext(msg.content)) return;

            const key = keyForChannel(channelId);
            if (!key) {
                toast("CipherChat: no key set for this channel — message not sent.", Toasts.Type.FAILURE);
                return { cancel: true };
            }

            try {
                const payload = await encrypt(msg.content, key);
                const cover = settings.store.coverText?.trim();
                const encrypted = cover ? `${cover}\n${wrap(payload)}` : wrap(payload);

                // Base64 inflates the text by ~4/3, so a message that fit the
                // 2000 char limit as plaintext may not fit encrypted.
                if (encrypted.length > MAX_MESSAGE_LENGTH) {
                    toast(
                        `CipherChat: encrypted message is ${encrypted.length}/${MAX_MESSAGE_LENGTH} characters — shorten it and try again.`,
                        Toasts.Type.FAILURE
                    );
                    return { cancel: true };
                }

                msg.content = encrypted;
            } catch (e) {
                console.error("[CipherChat] encryption failed", e);
                toast("CipherChat: encryption failed — message not sent.", Toasts.Type.FAILURE);
                return { cancel: true };
            }
        });
    },

    stop() {
        if (preSendListener) removeMessagePreSendListener(preSendListener);
        preSendListener = undefined;
        clearKeyCache();
        clearDecryptCache();
    }
});
