import { definePluginSettings } from "@api/Settings";
import { OptionType } from "@utils/types";

import { clearKeyCache } from "./crypto";
import { clearDecryptCache } from "./decryptcache";

export const settings = definePluginSettings({
    defaultKey: {
        type: OptionType.STRING,
        description:
            "Shared passphrase used when a channel has no key of its own. Anyone with this string can read your messages — treat it like a password.",
        default: "",
        onChange: () => {
            clearKeyCache();
            clearDecryptCache();
        }
    },
    encryptByDefault: {
        type: OptionType.BOOLEAN,
        description: "Start every channel with encryption on (otherwise toggle it per channel with the lock button).",
        default: false
    },
    coverText: {
        type: OptionType.STRING,
        description:
            "Optional plain-text line prepended to encrypted messages, so people without the plugin see something readable.",
        default: ""
    },
    showFingerprint: {
        type: OptionType.BOOLEAN,
        description: "Show a short key fingerprint next to decrypted messages, to confirm both sides use the same key.",
        default: false
    },
    markFailures: {
        type: OptionType.BOOLEAN,
        description: "Show a placeholder under messages you cannot decrypt instead of ignoring them silently.",
        default: true
    },

    // Internal persistence — managed by the plugin, not meant to be edited by hand.
    channelKeys: {
        type: OptionType.STRING,
        description: "Per-channel keys (managed by /cipherchat setkey)",
        default: "{}",
        hidden: true
    },
    enabledChannels: {
        type: OptionType.STRING,
        description: "Channels with encryption toggled on (managed by the lock button)",
        default: "[]",
        hidden: true
    }
});
