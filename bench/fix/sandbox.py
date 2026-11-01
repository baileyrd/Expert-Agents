"""The execution boundary for grading: run cargo on a run's code inside bubblewrap.

A graded run's files are model output; a `build.rs` or a test is code that runs. Grading
must not hand it the grader's environment, filesystem or network, so every cargo command
runs in a bubblewrap sandbox with:

- no network, and no other shared namespace (`--unshare-all`);
- an empty environment except PATH, HOME, the toolchain and the target dir;
- a minimal root: the system dirs and the toolchain read-only, the scratch tree and its
  target dir writable, a private /tmp, cargo's credential files masked. Nothing else of
  `$HOME` is mounted, so the target clone, this repo and `~/.claude` are not reachable.

Dependencies are fetched first, outside the sandbox, by `cargo fetch` (it downloads and
runs nothing), and the sandboxed command runs `--offline`. Grading fails closed: with no
bubblewrap on PATH it refuses to run.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

SYSTEM_DIRS = ("/usr", "/etc", "/bin", "/sbin", "/lib", "/lib64")
CREDENTIALS = ("credentials.toml", "credentials")


def toolchain() -> tuple[Path, Path]:
    """CARGO_HOME and RUSTUP_HOME, as cargo and rustup resolve them."""
    home = Path.home()
    cargo = Path(os.environ.get("CARGO_HOME", home / ".cargo"))
    rustup = Path(os.environ.get("RUSTUP_HOME", home / ".rustup"))
    return cargo, rustup


def bwrap() -> str:
    found = shutil.which("bwrap")
    if not found:
        raise ValueError("grading runs model-written code and needs bubblewrap (bwrap) on "
                         "PATH; refusing to run it on the host")
    return found


def wrap(argv: list[str], tree: Path, target: Path, cargo_home: Path, rustup_home: Path,
         bwrap_path: str) -> list[str]:
    """`argv` run at `tree` inside the sandbox. Pure: builds the command line only."""
    cmd = [bwrap_path, "--unshare-all", "--die-with-parent", "--new-session", "--clearenv"]
    for d in SYSTEM_DIRS:
        cmd += ["--ro-bind-try", d, d]
    cmd += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
    for d in (cargo_home, rustup_home):
        cmd += ["--ro-bind", str(d), str(d)]
    for name in CREDENTIALS:
        if (cargo_home / name).exists():
            cmd += ["--ro-bind", "/dev/null", str(cargo_home / name)]
    cmd += ["--bind", str(tree), str(tree), "--bind", str(target), str(target)]
    env = {"PATH": f"{cargo_home / 'bin'}:/usr/bin:/bin", "HOME": "/tmp", "TMPDIR": "/tmp",
           "CARGO_HOME": str(cargo_home), "RUSTUP_HOME": str(rustup_home),
           "CARGO_TARGET_DIR": str(target), "CARGO_NET_OFFLINE": "true"}
    toolchain_pin = os.environ.get("RUSTUP_TOOLCHAIN")
    if toolchain_pin:
        env["RUSTUP_TOOLCHAIN"] = toolchain_pin
    for key, value in env.items():
        cmd += ["--setenv", key, value]
    return [*cmd, "--chdir", str(tree), "--", *argv]
