# Building, signing and publishing releases

A release is three files: `atlas-release.json` (the manifest),
`atlas-release.json.sig`, and `atlas-<version>-aarch64.tar.xz`. Devices
trust a release only if the manifest is signed by a key in their keyring.

## Keys (once, offline)

```bash
# On an offline machine — never on a device, a CI runner or in this repo:
tools/release/keygen.py --out ~/atlas-keys/primary
tools/release/keygen.py --out ~/atlas-keys/backup      # escrow this one separately
```

Each command asks for a passphrase and writes `<name>.key` (encrypted) and
`<name>.pub`. Add both public entries to `system/keys/keyring.json` (it ships
empty, so a build with no production key can never be installed on a retail
unit). The backup key exists so a lost or compromised primary can be
replaced: sign a release whose keyring drops the old key and adds a new one
with the backup key; devices upgrade their keyring on commit. Revoked key ids
go in the keyring's revocation list.

Developer keys (`keygen.py --dev`) are unencrypted and trusted only on units
with `/etc/atlas/dev-mode`.

### Adding the production key (before the first retail release)

The keyring in this repository is still empty, so today only developer units
(which trust the developer key) can install a release; retail units and
`install.sh --from-github` on a retail unit refuse every release. To start
signing for everyone:

1. On your own offline machine (not this Jetson): run the two `keygen.py`
   commands above.
2. Put both public keys in `system/keys/keyring.json` with a higher
   `keyring_version`:
   ```json
   {"keyring_version": 2,
    "keys": [{"pub": "<primary.pub>", "status": "active"},
             {"pub": "<backup.pub>",  "status": "backup"}],
    "revoked": []}
   ```
   Commit and push.
3. Ship that keyring once to every existing unit: build a release from that
   commit and sign it with **both** keys, the developer key first, then
   `sign_release.py … --key ~/atlas-keys/primary.key` on your own machine
   (it adds its signature next to the developer one). Developer units install
   it through the developer key, retail units and new installs through the
   primary key, and each adopts the newer keyring when the release commits.
4. From then on, sign every release with the primary key and publish it;
   all units (and new installs, whose kit carries the keyring) accept it.

## Build

```bash
tools/release/lock.py            # when requirements change: pins requirements.lock with hashes
tools/release/build_release.py --version 1.2.0 --ref v1.2.0 --out dist --notes notes.txt
```

Run on an aarch64 machine with Python 3.12 and node (a Jetson works). The
bundle is `git archive` of the tag (minus `export-ignore` paths: mobile apps,
tests), the prebuilt content-hashed frontend (babel is not shipped), every
wheel from `requirements.lock`, and `release.json`. The tar is deterministic.
`seq` is the build's UTC epoch seconds — strictly increasing, which is how
devices refuse downgrades.

## Sign, verify, publish

A `.sig` can hold several signatures: signing with a second key adds it
(`--fresh` starts over), and a unit accepts the release if any of them is
from a key in its keyring.

```bash
tools/release/sign_release.py dist/1.2.0/atlas-release.json --key ~/atlas-keys/primary.key
tools/release/verify_release.py dist/1.2.0          # exactly the checks a device makes
tools/release/publish.sh dist/1.2.0                 # draft → upload → download and verify → publish
```

`publish.sh` uses the GitHub CLI. It creates a draft release `v1.2.0`,
uploads the three files, downloads them back and verifies them again, and
only then (after you confirm) publishes it; devices find it through
`…/releases/latest`. The releases URL is build-time configurable
(`ATLAS_RELEASES_URL`); the host is not trusted — only the signature is.

Every unit checks every 6 hours (and on Settings → Software Update → Check),
downloads in the background with resume, and installs on the owner's OK. The
same release also installs new units: `install.sh --from-github`
([INSTALL.md](INSTALL.md#installing-from-github)).

A release that changes root-owned code (`system/atlas_engine/`: identity,
system files, the engine itself) reaches existing units only if
`ENGINE_VERSION` in `system/atlas_engine/__init__.py` goes up; the new engine
is installed first and then installs the rest. (Engine 5: the `.local` Wi-Fi
name and the hotspot's DNS name. Engine 6: the factory reset forgets
Bluetooth devices; `atlas-engine check` reports the Atlas button.)

## Developer releases

```bash
tools/dev/deploy            # build this checkout, sign with the dev key, install through the engine
```

Only on developer units, which also keep SSH and trust the developer key.
