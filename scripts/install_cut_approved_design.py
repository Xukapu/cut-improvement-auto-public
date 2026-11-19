# -*- coding: utf-8 -*-
"""CUT Improvement Auto: approved NIGHT + LIGHT React UI installer.

Install from project/scripts. No changes to frontend/dist, release 0.1.0,
backend, PostgreSQL, MAX, photographs, .env or scheduled backups.
Requires source code matching the ZIP shared on 2026-11-05.
"""
from __future__ import annotations
import base64
import hashlib
import os
import shutil
import sys
import tempfile
import zlib
from datetime import datetime
from pathlib import Path

PACKAGE = {'frontend/src/App.tsx': {'old': '3257076d0889f11ea7361f71bea06a6429d7b09f6017adcf6dc53e85e938c72a', 'new': 'b0d4602e1ae89d518cb5605a57a43f49dfee978ebbbdd71e4c09d29192fffd9a', 'blob': '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'}, 'frontend/src/main.tsx': {'old': '09fd3a0608a3c47f82bdb41b169bc542e6674f4b576f38ba83c0c6558cc501d6', 'new': 'a3b980d2844c4b1aafb70e05df783a748784c1d870f5830832156b39ae104161', 'blob': 'eNpdUMFOwzAMvecrTA7TKo30A1omDbEDEgg0DpyjxB2BNZmSrB1C/XectFILPlnP7z0/27Rn5yP8MIC36I2Kz07jhg3QeNcC9yhV5BVjZuYpAiMenIv/eLfataU6GbRLye58nkiipF7EcF1MCTRW41WoEBLMZvc17dJOXVryE0eM+xOm9v77UacR0FZi8Q31xc2GFcIjOfk0q+dbtplapxRl6utyMSNV2mkaSCoe0HdG4bvzX+g5GJu1VnbmKKPzsFoRMAYXLUYp0Hbi9fDywIr8mZ5Ocb2QWu87SvpkQkQ7JiL3k5M6pwVYF3C3zZJUnTN63jKBAOJPnBn2eEzGk+1YvAy9+AyTfapiFigZ1ceSPa6/0LcaY1EvRFVuh/zTig2/nteXWA=='}, 'frontend/src/components/CutThemeControl.tsx': {'old': '29837febe431fb07ea53adce3e9c02eea7660bd7a3d0d543ca87ddce002cf1bc', 'new': 'ac8f0b8472106d53fd0ce730c59baf379c8ea36d6b2723ee7ff7844081515e6a', 'blob': 'eNqtVF1rE0EUfd9fcdmnBPKBLz602UgpQYqo0PRFpMhkdpIM3cyE2buJNQa0FXyRFv0HVvC5CAWx1t8w+4+8O7ubTW1BCz6EzMyee+65594ZOZlqg7CAJBa94VBwbGTLPjIUsISh0RPwjWAc/U1PluDHWqsG9BO1gkQJl6FolkgPD6cCthPcG4uJgAD8kJkDH14TUo7GGYRrFSP0957ubj3svXjUe5aheIJNymL0jMIUNlmCuokZR0Y6TBRHqRVQmrDPZiJ09LX6RpVq4QGgOXT/QEBMjIK5VKGetyLNWdRHbdhItEYCd1BMamsK6hAEQakQHqxWG4X8TeJcAmfIx9f51z57S89rt6EvEEg3THQoYCCG2gi3H0pDVe9mNsGUSUUoDWymZQgM5karUZPrSBsYRiwetwqXpJIoWeQq3Fi39U8jNr1Q8ySzrlUuepHI9wxZLLDFq+h1WrJXvHTtXblc5tnWCo2OanVXdK7ouWtKA4jRYfaJrpybThnYrVFM0L2hkmQS0WriClTu6F30Yy48C6taDrd2O77Z7UYeX88JrvcVgHqYp4lRRhHMtTmIyTDXRJ4YI7LWsQHMx0IRxmUBGUMoYzaIRNjKWbORaBRu7ed1FzNTc4BOKGfAqdfxEzYRgbsADtyM55L0COMDeU9fRkYnUx+YkawZsYGIAt+e2Qv7056D/ZW+o98b2lzS0ZX9np6C/Wq/2DPYqa4TbNF18rtFhZ1BgqhVsSML6coGfn7or05dvqkRcSzCYIG589UtWVbhEjOZ9pP9YS/Tk/Q9iThKP0D61n6zF+kRHR6nJ0AL0pweVxm02o4kPwgW+RiUE1UrMtTLFN1VRCd7eWL5SgSLe/eXucSxDEOhAh9NInxor4HjKVNd+7lUYc/T007bHZZGtPOi/5Mx7i34B1+O0o/Ur6u7uuLobzMle5Tv6spZruFvnnTaNKbZkiZ46f0GGW0ijQ=='}, 'frontend/src/components/DashboardVehicleStateCard.tsx': {'old': '934d8252bc37fa1bc9e21e6bb83515ec45a2e724164b22fcbb936998d7a528e3', 'new': '68077af0b550e96b656704fc8ca8321140ab750f08e5a7a42808245ceca23c75', 'blob': 'eNrtWG9v20QYf+9PcfKLKZGSmG3iTRunQuUtDIHEXqApcp1rYsW+s86XlhAsrQO0oSGGQGKv4ROU0rDQkfEVzl+BT8JzZ6f+EzsJUbdVqKnS5O75/7vn7uec4/mUcTTRENq3WEN+DLA93HeY7eI7cnzfcl3MQdgL1JBhYg8aWogOGfWQ7o5sp4ebDFs213c1zYkd8rGPldf3rWBwQMH6Yxz4lAQ4NW21DKkWGL2FTsaBtD2kzLP4B5Tgcc5qxB03MGKpNNFUtI8Y9QNkKstLjzvLCaAvERm57i6oUXLPx+Q936cO4R4mPNhBtToyO+iIOr1U4z5lw3ush1lBHsrg+HOV8OGI2NyhJA34KR44gOIn3OJY4lfLZdYojd8oiQm178TV1VVxNlTBkUNgqs9wADXDZMbzXsshXX8h3NtDnz2APBd2sFK98bJJPF1U7uEDXuI/ni4qY8/nC89pdi0Xkz4fINM00Tvo1i0lVtFKJcp1TiIxlhZ8xAiqKaW2xbiEFtmuFQQfWh429WPAK/AtGzdtyBEdxeA3CT1WE3pHWYJtzznK2klhcwD5YHapE2ulIxiDa7JkNnTsYc5MvsSvYiouoq+ib+FziqIT8So6iR7B/2diLmZimnVrSL8dLTs1uFvw94M4k+biL3j/Bg5eipn0OhV/Ro/FaXSS85e1bhuqinQMXYgC5wtsTu7cDpFxCUmilwwn8ULuJWCXgZZFV2nnMGhnz5BcLUnwu2Fm1siXXwBeQs8ZJf38pILlAmCZiTMxj55G36DoIUAiMZortM4QoD2NHuV9GQtnhRBqFQoBfkkhTnwh8H8aPYGg86LymYx/CuvzCqJPG0j8rr5BHio5cYHkN3EO9o9zLaCsF4uKxAtwcg5mL8UfEH4Of6p7ou+i71vFUgpJJ8tYOqyjnQ2Xs8/oyA/0HEKT5e3cUVs24zPGEcdn4CrfehHoVdnIjemQ/pINWMVMlLTU7XfDQiOtbB4F+o+5JVs2Le+Vyn4pByos81tiXdyr66FxnYCX4JJNwbP8WkmWNSp5RfFYmTgOWypAVckweqxXWAzx2JxUyIDsZCqVUoRa8lzvKq0uGXkHlcph6XynQnv5mNmway5hXiFbX5WqLMGvSwDNFbphdZrVHbq2U6+wDtexMTxXdX0XHnS2LWRFmuVbI63QgyfU6hLFc9jjF/B+gf75+mdtGxzWo7Bxl1ajABhUF1I42tNXvbE0WQ+1tcYQLD6ps9NgmDv0cw9pb/+8r3yoyD5aAA8syf4rL/yUMvca3t6SJLLAvnl+iKPfUMMNNdxQww01bEcNuV/p1+CnQHpH9VqZ4XnVj7Or4YUsqm+eF+LoVbwgpdeDFmQm/ztWWFOUKsx2HUz42+WEDY/Mqyh383Pz9fJHVQPLGvRqrCaZ2+uathoKdaHaqFSqX2tiKL9vCquuW0e8uUAxGFDGYSLIXrwejDgH2pBX+qYeD/SiA0vl0fSZ41lsrCNK9uFJY2hOli/Uw45q2b/FLDoRs7YRe9wuHtRPSa8sYnpZn8RTW6QJBHEaPYyeifPoaTF0Bq62kdxny2F9Vwv/BWiOQsQ='}, 'frontend/src/styles/cut-dashboard-layout.css': {'old': None, 'new': 'b80124331f220747fa01ce2a0db7dcd15d61d1f261d4ef04a1459497e1a3ae9a', 'blob': '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'}}
ASSETS = {'frontend/public/cut-night-assets/improvement-auto-logo.png': '5cd04ae9ab8de71eb6f7cb17de8b19feebc4355619a1e935e43f54ecc9eb9075', 'frontend/public/cut-night-assets/night-hero.jpg': '43c77bee82146418c6e713cafc37bba2fb429c7d8e42e610144cd04e165dee58', 'frontend/src/styles/cut-night-drive.css': '323959b176108631dc0d71b42b63df9661b5014c648bc9926dbec5149444bc84', 'frontend/src/styles/cut-light-drive.css': 'e0c9e3baa495eeea2f9c28f2bd6e76918f68c623ca9fea9cbfa573798dd40a29'}

def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.cut-design-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)

def main() -> int:
    print('=== CUT Improvement Auto / Approved Night + Light Design ===')
    script = Path(__file__).resolve()
    root = script.parent.parent
    if script.parent.name.lower() != 'scripts' or not (root/'frontend'/'src'/'App.tsx').is_file() or not (root/'backend'/'app'/'main.py').is_file():
        raise RuntimeError('Place the installer in PROJECT/scripts. No files changed.')

    # Preflight fully, BEFORE modifying any file.
    for rel, expected in ASSETS.items():
        path = root / rel
        if not path.is_file() or digest(path.read_bytes()) != expected:
            raise RuntimeError('Missing or unexpected prerequisite: ' + rel + '. Please stop: no files changed.')
    actions = {}
    for rel, record in PACKAGE.items():
        path = root / rel
        content = zlib.decompress(base64.b64decode(record['blob']))
        if digest(content) != record['new']:
            raise RuntimeError('Embedded payload damaged: ' + rel)
        current = path.read_bytes() if path.is_file() else None
        if current is not None and digest(current) == record['new']:
            continue
        if record['old'] is None:
            if current is not None:
                raise RuntimeError('Refusing to overwrite an existing custom file: ' + rel)
        elif current is None or digest(current) != record['old']:
            raise RuntimeError('Source file differs from uploaded snapshot. Please do not overwrite it: ' + rel)
        actions[path] = content
    if not actions:
        print('OK: Approved design already installed; no changes needed.')
        return 0

    stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    backup = root.parent/'CUT_Improvement_Auto_DevArchive'/'approved_design_backups'/stamp
    backup.mkdir(parents=True, exist_ok=False)
    before = {p:p.read_bytes() if p.exists() else None for p in actions}
    for path, content in before.items():
        if content is None:
            continue
        copy = backup / path.relative_to(root)
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, copy)
        if digest(copy.read_bytes()) != digest(content):
            raise RuntimeError('Backup verification failed. No source files changed.')

    # New CSS first; replace main.tsx last to avoid a half-enabled UI.
    paths = sorted(actions, key=lambda p:(p.name == 'main.tsx',p.name == 'App.tsx',str(p)))
    written = []
    try:
        for path in paths:
            write_atomic(path, actions[path])
            if digest(path.read_bytes()) != digest(actions[path]):
                raise RuntimeError('Post-write SHA-256 mismatch: '+str(path))
            written.append(path)
    except BaseException:
        for path in reversed(written):
            original = before[path]
            if original is None:
                path.unlink(missing_ok=True)
            else:
                write_atomic(path, original)
        print('ERROR: Reverted written files. Backup: '+str(backup),file=sys.stderr)
        raise
    print('OK: Real React layout now includes the company logo, top masthead and automotive hero.')
    print('OK: Dashboard order, KPI cards, menu and navigation updated.')
    print('OK: Night + Light themes with integrated persistent selector.')
    print('OK: Existing dashboard API fields retained; no invented revenue figures.')
    print('BACKUP: '+str(backup))
    print('CHANGED: '+', '.join(str(p.relative_to(root)) for p in paths))
    print('UNCHANGED: frontend/dist, active release 0.1.0, backend, PostgreSQL, MAX, .env, photos, backups, Git.')
    print('NEXT: npm --prefix .\\frontend run dev -- --host 127.0.0.1')
    print('PREVIEW: http://127.0.0.1:5173/ (do not build or activate yet)')
    return 0

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print('INSTALL FAILED: '+str(exc),file=sys.stderr)
        raise SystemExit(1)
