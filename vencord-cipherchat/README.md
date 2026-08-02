# CipherChat

A [Vencord](https://vencord.dev) userplugin that encrypts your messages **on your machine**
before they're sent. Discord stores an opaque blob; anyone else running CipherChat with the
same key sees the real text rendered underneath the message.

```
you type:      meet at 8
what's sent:   🔒cc1:AYkZ6E0eXyzzVND1dfrAI5o0UPmjvnpXdNKFUWjRktjt0bLNVhl-Ks…🔒
they see:      🔒cc1:AYkZ…🔒
               │ meet at 8          ← rendered by their CipherChat
```

## Crypto

| | |
|---|---|
| Cipher | AES-256-GCM (authenticated — tampering is detected, not silently decrypted) |
| Key derivation | PBKDF2-SHA256, 210,000 iterations |
| IV | 12 random bytes per message |
| Wire format | `🔒cc1:` + base64url(`version` ‖ `iv[12]` ‖ `ciphertext+tag`) + `🔒` |

The PBKDF2 salt is derived deterministically from the passphrase (`SHA-256("cipherchat/v1/salt|" + passphrase)`).
That's what lets two clients who share *only* the passphrase arrive at the same key, but it also
means a weak passphrase is open to precomputation. **Use `/cipherchat genkey`** — it produces a
256-bit random key — rather than inventing one.

## Install

Requires a [self-built Vencord](https://docs.vencord.dev/installing/) (userplugins don't work
with the standard installer).

```bash
cd Vencord
mkdir -p src/userplugins
cp -r /path/to/vencord-cipherchat src/userplugins/cipherchat
pnpm build
pnpm inject   # if not already injected
```

Restart Discord, then enable **CipherChat** in Vencord → Plugins.

## Use

1. `/cipherchat genkey` in the channel you want to encrypt. Copy the key it prints.
2. Get that key to the other person **outside Discord** (Signal, in person, a password manager
   share — anywhere the party you're hiding from can't read).
3. They run `/cipherchat setkey key:<the key>` in the same channel.
4. Click the 🔒 button in the chat bar to turn encryption on for the channel. Everything you
   send from then on is encrypted.

### Commands

| Command | What it does |
|---|---|
| `/cipherchat genkey` | Generate a random 256-bit key and use it in this channel |
| `/cipherchat setkey key:<key>` | Use a specific key in this channel |
| `/cipherchat clearkey` | Forget this channel's key (falls back to the default key) |
| `/cipherchat toggle` | Turn encryption on/off here (same as the 🔒 button) |
| `/cipherchat status` | Show whether encryption is on and which key is active |

All command output is a local-only bot message — nothing is sent to the channel.

### Settings

- **Default key** — used in channels with no key of their own.
- **Encrypt by default** — start every channel with encryption on.
- **Cover text** — a plaintext line prepended to encrypted messages, so people without the
  plugin see something readable instead of just a blob.
- **Show fingerprint** — print a short key fingerprint next to decrypted messages, so both
  sides can confirm they're on the same key without comparing the key itself.
- **Mark failures** — show a placeholder under messages you can't decrypt instead of ignoring
  them.

Incoming messages are tried against the channel key, the default key, and then every other
channel key you have, so old messages stay readable after you rotate a key.

## Limits — read these

- **Only message text is encrypted.** Attachments, embeds, message edits made outside the
  plugin, reactions, usernames, and all metadata (who you talk to, when, how often) are
  untouched. Discord still sees all of it.
- **This is not a security guarantee.** It runs inside a client Discord ships and can update.
  It defeats server-side reading of message *contents*; it does not defeat a compromised
  machine, a screenshot, or the person you're talking to.
- **Key distribution is on you.** There is no key exchange or forward secrecy — one shared
  static key per channel. If the key leaks, all past messages encrypted under it are readable.
- **Keys are stored in plain text** in your Vencord settings (`settings.json`), as with any
  Vencord plugin setting.
- Client mods are against Discord's Terms of Service. Enforcement against Vencord users is
  rare, but the risk is yours.
- Encrypted messages are ~1.4× longer than the plaintext; sends over the 2000-character limit
  are blocked with a toast rather than truncated.

## Layout

```
index.tsx                        plugin definition, chat-bar button, commands, send hook
crypto.ts                        AES-GCM + PBKDF2, wire format, no Vencord dependencies
keystore.ts                      per-channel keys and on/off state
settings.ts                      Vencord settings schema
components/DecryptedAccessory.tsx  renders plaintext under encrypted messages
```

`crypto.ts` is standalone and dependency-free, so it can be compiled and exercised outside
Discord (`tsc crypto.ts --target es2022 --module es2022 --lib es2022,dom --strict`).
