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

## Developer releases

```bash
tools/dev/deploy            # build this checkout, sign with the dev key, install through the engine
```

Only on developer units, which also keep SSH and trust the developer key.

To update developer units from Settings → Software Update instead, publish a
developer release. Pushing to `main` alone never reaches the button: it only
sees releases.

```bash
V=$(cat VERSION)-dev.$(date -u +%Y%m%d%H%M%S)
tools/release/build_release.py --version "$V" --ref main --channel dev --out dist --notes notes.txt
tools/release/sign_release.py dist/$V/atlas-release.json --key ~/.config/atlas-dev/dev-release.key --yes
tools/release/publish.sh dist/$V --dev-key /etc/atlas/dev-keys/dev.pub
```

It becomes `…/releases/latest` for every unit. Units without the developer key
report "Update could not be verified". That is harmless while no production
release exists, but once one does, a developer release published there would
hide it from retail units. From then on, publish developer releases to a
separate repo (`--repo`, with `--url-base` and `ATLAS_RELEASES_URL` pointing at it).
