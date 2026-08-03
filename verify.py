#!/usr/bin/env python3
"""
Verify and manage ansible-vault encryption for sensitive files listed in .gitignore.

Usage:
    python verify.py                 # Check encryption status of all files
    python verify.py encrypt         # Encrypt all unencrypted files
    python verify.py decrypt         # Decrypt all encrypted files
    python verify.py install-hook    # Install git pre-commit hook
"""

import os
import stat
import sys
import subprocess

VAULT_HEADER = "$ANSIBLE_VAULT;"

# Entries from .gitignore to skip (not sensitive data)
# - venv: Python virtualenv, not project data
# - .history: editor local history, not project data
# - tests/render/out: golden-file test harness output (tests/run.sh), regenerated
#   on every run — build output, not sensitive data
SKIP_ENTRIES = {"venv", ".history", "tests/render/out"}

PRE_COMMIT_HOOK = """\
#!/bin/sh
# Ansible Vault encryption guard — installed by verify.py
# Blocks commits if any sensitive file is decrypted or .gitignore is missing entries.

python3 "$(git rev-parse --show-toplevel)/verify.py" --hook-check
"""


def get_project_root():
    return os.path.dirname(os.path.abspath(__file__))


def read_gitignore(root):
    """Read .gitignore and return non-empty, non-comment entries."""
    path = os.path.join(root, ".gitignore")
    if not os.path.exists(path):
        print("ERROR: .gitignore not found")
        sys.exit(1)

    with open(path) as f:
        entries = []
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                entries.append(line)
    return entries


def resolve_files(root, entries):
    """Expand .gitignore entries into a list of existing file paths."""
    files = []
    for entry in entries:
        if entry in SKIP_ENTRIES:
            continue

        full = os.path.join(root, entry)
        if os.path.isfile(full):
            files.append(full)
        elif os.path.isdir(full):
            for dirpath, _, filenames in os.walk(full):
                for fname in sorted(filenames):
                    files.append(os.path.join(dirpath, fname))
    return files


def sensitive_entries(entries):
    """Return .gitignore entries that represent sensitive data (excluding skipped)."""
    return [e for e in entries if e not in SKIP_ENTRIES]


def is_vault_encrypted(filepath):
    """Check if a file starts with the ansible-vault header."""
    try:
        with open(filepath, "rb") as f:
            first_line = f.readline().decode("utf-8", errors="ignore").strip()
            return first_line.startswith(VAULT_HEADER)
    except (IOError, OSError):
        return False


def is_tracked_by_git(root, filepath):
    """Check if a file is tracked in the git index."""
    rel = os.path.relpath(filepath, root)
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", rel],
        cwd=root, capture_output=True,
    )
    return result.returncode == 0


def set_skip_worktree(root, filepath, enable):
    """Set or unset skip-worktree flag on a tracked file."""
    rel = os.path.relpath(filepath, root)
    flag = "--skip-worktree" if enable else "--no-skip-worktree"
    subprocess.run(
        ["git", "update-index", flag, rel],
        cwd=root, capture_output=True,
    )


def run_vault_command(action, filepath):
    """Run ansible-vault encrypt/decrypt on a file."""
    result = subprocess.run(
        ["ansible-vault", action, filepath],
        capture_output=False,
    )
    return result.returncode == 0


def report_status(root, files):
    """Print encryption status of all tracked files."""
    print(f"\n{'File':<50} {'Status'}")
    print("-" * 65)

    encrypted_count = 0
    plain_count = 0

    for fpath in files:
        rel = os.path.relpath(fpath, root)
        if is_vault_encrypted(fpath):
            status = "ENCRYPTED"
            encrypted_count += 1
        else:
            status = "NOT ENCRYPTED"
            plain_count += 1
        print(f"  {rel:<48} {status}")

    print("-" * 65)
    print(f"  Total: {len(files)}  |  Encrypted: {encrypted_count}  |  Plain: {plain_count}")
    return plain_count


def encrypt_all(root, files):
    """Encrypt all unencrypted files, then remove skip-worktree."""
    targets = [f for f in files if not is_vault_encrypted(f)]
    if not targets:
        print("\nAll files are already encrypted.")
        return True

    print(f"\nEncrypting {len(targets)} file(s):")

    ok = True
    for fpath in targets:
        rel = os.path.relpath(fpath, root)
        if run_vault_command("encrypt", fpath):
            print(f"  [OK]   {rel}")
        else:
            print(f"  [FAIL] {rel}")
            ok = False

    # Remove skip-worktree so git can see the encrypted versions
    for fpath in files:
        if is_tracked_by_git(root, fpath):
            set_skip_worktree(root, fpath, enable=False)

    return ok


def decrypt_all(root, files):
    """Decrypt all encrypted files, then set skip-worktree on tracked ones."""
    targets = [f for f in files if is_vault_encrypted(f)]
    if not targets:
        print("\nAll files are already decrypted.")
        return True

    print(f"\nDecrypting {len(targets)} file(s):")

    ok = True
    for fpath in targets:
        rel = os.path.relpath(fpath, root)
        if run_vault_command("decrypt", fpath):
            print(f"  [OK]   {rel}")
        else:
            print(f"  [FAIL] {rel}")
            ok = False

    # Set skip-worktree on tracked files to hide decrypted content from git
    protected = 0
    for fpath in files:
        if is_tracked_by_git(root, fpath):
            set_skip_worktree(root, fpath, enable=True)
            protected += 1

    if protected:
        print(f"\n  skip-worktree set on {protected} tracked file(s)")
    else:
        print("\n  Note: files are untracked (.gitignore), skip-worktree not needed")

    return ok


def install_hook(root):
    """Install git pre-commit hook."""
    hooks_dir = os.path.join(root, ".git", "hooks")
    hook_path = os.path.join(hooks_dir, "pre-commit")

    if not os.path.isdir(hooks_dir):
        print("ERROR: .git/hooks directory not found. Is this a git repository?")
        return False

    # Check for existing hook
    if os.path.exists(hook_path):
        with open(hook_path) as f:
            content = f.read()
        if "verify.py" in content:
            print("Pre-commit hook is already installed.")
            return True
        print(f"WARNING: A pre-commit hook already exists at {hook_path}")
        print("Appending vault check to existing hook.\n")
        # Append our check (skip the shebang line)
        lines = PRE_COMMIT_HOOK.strip().splitlines()
        append_content = "\n" + "\n".join(lines[1:]) + "\n"
        with open(hook_path, "a") as f:
            f.write(append_content)
    else:
        with open(hook_path, "w") as f:
            f.write(PRE_COMMIT_HOOK)

    # Make executable
    st = os.stat(hook_path)
    os.chmod(hook_path, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    print(f"Pre-commit hook installed at {hook_path}")
    print("Commits will be blocked if any sensitive file is not vault-encrypted.")
    return True


def hook_check(root, entries, files):
    """Called by the pre-commit hook. Returns exit code 0 (ok) or 1 (block)."""
    errors = []

    # 1. Check that .gitignore still contains all expected sensitive entries
    current_entries = set(read_gitignore(root))
    for entry in sensitive_entries(entries):
        if entry not in current_entries:
            errors.append(f".gitignore is missing entry: {entry}")

    # 2. Check that all existing sensitive files are encrypted
    for fpath in files:
        if not is_vault_encrypted(fpath):
            rel = os.path.relpath(fpath, root)
            errors.append(f"NOT ENCRYPTED: {rel}")

    # 3. Check staged files — catch force-added sensitive files
    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=root, capture_output=True, text=True,
    )
    if staged.returncode == 0:
        staged_files = set(staged.stdout.strip().splitlines())
        sensitive = {e for e in sensitive_entries(entries)}
        for sf in staged_files:
            # Direct match or under a sensitive directory
            for entry in sensitive:
                if sf == entry or sf.startswith(entry + "/"):
                    full = os.path.join(root, sf)
                    if os.path.isfile(full) and not is_vault_encrypted(full):
                        rel = os.path.relpath(full, root)
                        msg = f"STAGED & NOT ENCRYPTED: {rel}"
                        if msg not in errors and f"NOT ENCRYPTED: {rel}" not in errors:
                            errors.append(msg)

    if errors:
        print("COMMIT BLOCKED — vault encryption check failed:\n")
        for e in errors:
            print(f"  ✗ {e}")
        print("\nRun 'python verify.py encrypt' to fix, then retry your commit.")
        return 1

    return 0


def main():
    root = get_project_root()
    entries = read_gitignore(root)
    files = resolve_files(root, entries)

    action = sys.argv[1] if len(sys.argv) > 1 else None

    # Hidden flag used by the pre-commit hook
    if action == "--hook-check":
        sys.exit(hook_check(root, entries, files))

    if not files:
        print("No sensitive files found to check.")
        sys.exit(0)

    if action == "encrypt":
        report_status(root, files)
        ok = encrypt_all(root, files)
        print()
        report_status(root, files)
        sys.exit(0 if ok else 1)

    elif action == "decrypt":
        report_status(root, files)
        ok = decrypt_all(root, files)
        print()
        report_status(root, files)
        sys.exit(0 if ok else 1)

    elif action == "install-hook":
        ok = install_hook(root)
        sys.exit(0 if ok else 1)

    elif action is None:
        plain = report_status(root, files)
        if plain > 0:
            print(f"\nWARNING: {plain} file(s) are NOT encrypted.")
            print("Run 'python verify.py encrypt' to encrypt them.")
            sys.exit(1)
        else:
            print("\nAll files are encrypted.")
            sys.exit(0)

    else:
        print(f"Unknown action: {action}")
        print(__doc__.strip())
        sys.exit(2)


if __name__ == "__main__":
    main()
