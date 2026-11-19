# -*- coding: utf-8 -*-
r"""Isolated frontend update/rollback rehearsal for CUT Improvement Auto.

Run from the project root:
    .\.venv\Scripts\python.exe .\scripts\test_cut_frontend_update_sandbox.py

No application files, databases, backup folders, services, tasks or Git files are changed.
The test builds a dummy update package from frontend/dist and works only in a
fresh temporary directory. This is NOT a production updater or real update.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZipFile

MAX_TOTAL_BYTES = 250 * 1024 * 1024
MAX_FILES = 10000


def fail(message: str) -> None:
    raise RuntimeError(message)


def sha_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot(folder: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    total = 0
    for base, folders, names in os.walk(folder, followlinks=False):
        current = Path(base)
        for name in folders:
            if (current / name).is_symlink():
                fail("Symlinked folder in frontend/dist: " + str(current / name))
        for name in names:
            path = current / name
            if path.is_symlink() or not path.is_file():
                fail("Unexpected file in frontend/dist: " + str(path))
            rel = path.relative_to(folder).as_posix()
            total += path.stat().st_size
            if total > MAX_TOTAL_BYTES or len(result) >= MAX_FILES:
                fail("Frontend build exceeds test limits")
            result[rel] = sha_file(path)
    return result


def _json_bytes(data: object) -> bytes:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")


def make_package(source: Path, destination: Path, version: str, *, corrupt: bool = False) -> int:
    files = snapshot(source)
    if "index.html" not in files or not any(path.startswith("assets/") for path in files):
        fail("frontend/dist must contain index.html and assets")
    manifest = {
        "format": "cut-frontend-update-test-v1",
        "version": version,
        "files": files,
    }
    with ZipFile(destination, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", _json_bytes(manifest))
        for index, rel in enumerate(sorted(files)):
            payload = (source / rel).read_bytes()
            if corrupt and index == 0:
                payload += b"\nTAMPERED_TEST_CONTENT\n"
            archive.writestr("frontend/" + rel, payload)
    return len(files)


def verify_package(package: Path, staging_dir: Path) -> str:
    staging_dir.mkdir(parents=True, exist_ok=False)
    with ZipFile(package) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or len(names) > MAX_FILES + 1:
            fail("Duplicate or excessive ZIP members")
        if "manifest.json" not in names:
            fail("Package manifest missing")
        manifest_raw = archive.read("manifest.json")
        if len(manifest_raw) > 2 * 1024 * 1024:
            fail("Package manifest oversized")
        manifest = json.loads(manifest_raw.decode("utf-8"))
        if manifest.get("format") != "cut-frontend-update-test-v1":
            fail("Unexpected package type")
        files = manifest.get("files")
        if not isinstance(files, dict) or not files or len(files) > MAX_FILES:
            fail("Invalid manifest file list")
        version = manifest.get("version")
        if not isinstance(version, str) or not version.startswith("0."):
            fail("Invalid test version")
        listed = {"frontend/" + rel for rel in files}
        if set(names) != listed | {"manifest.json"}:
            fail("Package files do not match manifest")
        total = 0
        for info in archive.infolist():
            name = info.filename
            posix = PurePosixPath(name)
            if (
                info.is_dir()
                or name.startswith("/")
                or "\\" in name
                or ":" in name
                or ".." in posix.parts
                or any(part in {"", "."} for part in posix.parts)
                or (info.external_attr >> 16) & 0o170000 == 0o120000
            ):
                fail("Unsafe ZIP entry")
            total += info.file_size
            if total > MAX_TOTAL_BYTES:
                fail("Update package exceeds size limit")
        for rel, digest in files.items():
            if not isinstance(rel, str) or not isinstance(digest, str):
                fail("Invalid manifest entry")
            pure = PurePosixPath(rel)
            if (
                rel.startswith("/")
                or "\\" in rel
                or ":" in rel
                or ".." in pure.parts
                or not rel
                or len(digest) != 64
                or any(c not in "0123456789abcdef" for c in digest.lower())
            ):
                fail("Unsafe manifest entry")
            content = archive.read("frontend/" + rel)
            if sha_bytes(content) != digest:
                fail("SHA-256 mismatch detected: " + rel)
            out = staging_dir.joinpath(*pure.parts)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(content)
        if not (staging_dir / "index.html").is_file():
            fail("Staged index.html missing")
    return version


def set_active(pointer: Path, version: str) -> None:
    pending = pointer.with_suffix(".new")
    pending.write_text(version + "\n", encoding="utf-8")
    pending.replace(pointer)


def main() -> None:
    root = Path.cwd().resolve()
    required = [
        root / "backend" / "app" / "main.py",
        root / "frontend" / "package.json",
        root / "frontend" / "dist" / "index.html",
        root / "scripts" / "backup_cut.py",
    ]
    if not all(p.is_file() for p in required):
        raise SystemExit("ERROR: run from the expected CUT Improvement Auto project root; nothing changed")

    frontend = root / "frontend" / "dist"
    before_frontend = snapshot(frontend)
    protected_files = [root / "backend" / "app" / "main.py", root / "scripts" / "backup_cut.py"]
    before_protected = {str(file): sha_file(file) for file in protected_files}
    if not before_frontend:
        fail("frontend/dist is empty")

    print("=== CUT FRONTEND UPDATE: ISOLATED SANDBOX TEST ===")
    print("MODE: temporary simulation, no real update")
    print("SOURCE: frontend/dist (read-only)")

    with tempfile.TemporaryDirectory(prefix="cut_update_sandbox_") as temp:
        sandbox = Path(temp).resolve()
        if sandbox == root or root in sandbox.parents:
            fail("Temporary directory is inside project; refusing test")
        releases = sandbox / "releases"
        releases.mkdir()
        current = releases / "0.1.0"
        shutil.copytree(frontend, current)
        pointer = sandbox / "active_version.txt"
        set_active(pointer, "0.1.0")
        shared = sandbox / "shared_data"
        shared.mkdir()
        dummy_photo = shared / "customer_photo.jpg"
        dummy_photo.write_bytes(b"CUT TEST ONLY: placeholder photo, not a real user file")
        dummy_secret = shared / "settings.env"
        dummy_secret.write_text("TEST_PLACEHOLDER=not_a_real_secret\n", encoding="utf-8")
        protected_data = {path.name: sha_file(path) for path in (dummy_photo, dummy_secret)}

        candidate = sandbox / "candidate"
        shutil.copytree(frontend, candidate)
        (candidate / "cut_sandbox_update_probe.txt").write_text(
            "Test frontend update 0.1.1; exists in sandbox only.\n", encoding="utf-8"
        )
        package = sandbox / "frontend_test_update.zip"
        total = make_package(candidate, package, "0.1.1")
        staged = releases / "0.1.1"
        version = verify_package(package, staged)
        if snapshot(staged) != snapshot(candidate):
            fail("Staged files differ from packaged files")
        print(f"OK: SHA-256 package verification ({total} sandbox files)")

        set_active(pointer, version)
        if pointer.read_text(encoding="utf-8").strip() != "0.1.1":
            fail("Simulated update did not activate")
        print("OK: simulated frontend update 0.1.0 -> 0.1.1")
        set_active(pointer, "0.1.0")
        if pointer.read_text(encoding="utf-8").strip() != "0.1.0":
            fail("Simulated rollback did not restore previous version")
        print("OK: simulated rollback 0.1.1 -> 0.1.0")

        corrupt = sandbox / "corrupted_package.zip"
        make_package(candidate, corrupt, "0.1.1", corrupt=True)
        try:
            verify_package(corrupt, sandbox / "rejected_candidate")
        except (ValueError, RuntimeError) as exc:
            if "SHA-256 mismatch" not in str(exc):
                raise
            print("OK: corrupted update package rejected before activation")
        else:
            fail("Corrupted update was not rejected")
        if pointer.read_text(encoding="utf-8").strip() != "0.1.0":
            fail("Corrupted update unexpectedly changed active version")
        if {path.name: sha_file(path) for path in (dummy_photo, dummy_secret)} != protected_data:
            fail("Dummy shared data changed")
        print("OK: simulated customer data and settings unaffected")

    if snapshot(frontend) != before_frontend:
        fail("Unexpected change in real frontend/dist")
    if any(sha_file(file) != before_protected[str(file)] for file in protected_files):
        fail("Unexpected change in real backend or backup scripts")
    print("SUCCESS: ISOLATED FRONTEND UPDATE AND ROLLBACK TEST PASSED")
    print("REAL APP, DATABASE, PHOTOS, BACKUPS, WINDOWS TASKS, GIT: UNTOUCHED")
    print("NOTE: this is a sandbox prototype, not a production updater")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit("TEST FAILED: " + f"{type(error).__name__}: {error}") from error
