---
name: mirror
description: Publish a signed EmiBSD release's install tree to cdn.emibsd.org (Cloudflare R2) and check it from the public side. Use after an OS release with `[release] signify` is published on GitHub, or when the user asks to update, check or repair the mirror.
---

# The mirror

`cdn.emibsd.org/pub/EmiBSD/<osrev>/<arch>/` is where the installer (network install), sysupgrade
and syspatch look for a release. It holds the release's `<osrev>/<arch>/` tree as `just media`
makes it, minus `cdbr`: bsd.mp, bsd.rd, the four sets, INSTALL.<arch>, SHA256 and SHA256.sig.
`tools/mirror.py` does the work; `docs/MEDIA.md` ("The mirror") is the reference.

Uploading publishes: it runs from the main session only, never from a subagent, and only with
the user's go-ahead for that release (see below).

## Steps

1. The release must be signed and published: `[release] signify` is set, the tag `<version>`
   exists on origin, and its GitHub release is out of draft. Otherwise stop and say so.
2. The local tree must be that release's. Rebuild it at the tag's time, then compare with what
   GitHub serves:
   ```sh
   SOURCE_DATE_EPOCH=$(git log -1 --format=%ct <version>) python3 -I tools/media.py build
   gh api repos/EmiBSD/OS/releases/tags/<version> --jq '.assets[]|.name+" "+.digest'
   cat dist/<version>/SHA256
   ```
   Every image's sum must equal its asset's digest. If not, stop: the tree is not the release's.
   (`just media` takes the epoch of HEAD, which is the release's only while HEAD is the tag.)
3. `just mirror plan`: what will be uploaded and deleted. Show it to the user.
4. With the user's go-ahead, `just mirror`: uploads in order (everything else, SHA256,
   SHA256.sig), deletes what is gone, copies the files inside the bucket to
   `pub/EmiBSD/releases/<version>/<arch>/` (kept for good), then runs the public check.
5. Report the check's lines as they are: each arch's download, the signature with
   `keys/<key>.pub`, the SHA256 against the local tree, and the mirror list
   (`ftplist1.emibsd.org/cgi-bin/ftplist.cgi`, which must name `https://cdn.emibsd.org/pub/EmiBSD`).
   A failure is reported, never retried blindly: say what failed and ask.

`just mirror check` alone re-runs step 5 at any time.

## Rules

- The credentials file (`$EMIBSD_R2_CREDS`, default `~/emibsd-keys/emibsd-r2`, mode 0600) is
  sourced by the shell that runs `aws`. Never cat, echo, copy or log it; never put its values
  in a command line, a file or a message.
- `cgi-bin/ftplist.cgi` in the bucket belongs to the mirror's setup; leave it.
- Every object is `Cache-Control: max-age=300`, since names repeat between releases.
- The CDN cache is not purged: CLOUDFLARE_TOKEN's scopes are unchecked. A stale object clears
  in five minutes; the check asks with `Cache-Control: no-cache` and a query string.

## Later, not done yet

- `ftp.emibsd.org/pub/EmiBSD/signify/sigbundle-emibsd-NN-base.tgz`, which sysupgrade fetches
  from 8.2 on when the next release's key is missing; not a domain yet.
- `syspatch/<osrev>/<arch>/` once there are EmiBSD errata.
- `emibsd-firmware` (firmware.emibsd.org, plain http too, for fw_update), once there is real
  hardware.

Not OS's: `emibsd-man` (man.emibsd.org) is Userland's, which makes the man set and the mandoc
that renders it (the user, 2026-11-07); leave that bucket alone.
