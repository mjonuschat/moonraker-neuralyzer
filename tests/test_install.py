import os
import subprocess
from pathlib import Path


def _run_install(repo_root: Path, moonraker_root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(repo_root / "install.sh"), str(moonraker_root)],
        capture_output=True,
        text=True,
    )


def _fake_moonraker_checkout(tmp_path: Path) -> Path:
    moonraker = tmp_path / "moonraker"
    (moonraker / "moonraker" / "components").mkdir(parents=True)
    (moonraker / ".git" / "info").mkdir(parents=True)
    (moonraker / ".git" / "info" / "exclude").touch()
    return moonraker


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _fake_upstream_repo(tmp_path: Path) -> Path:
    """A local git repo standing in for the real GitHub remote."""
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    _git("init", "-q", cwd=upstream)
    (upstream / "neuralyzer.py").write_text("# stand-in for the real component\n")
    _git("add", "neuralyzer.py", cwd=upstream)
    _git(
        "-c",
        "user.email=test@example.com",
        "-c",
        "user.name=test",
        "commit",
        "-q",
        "-m",
        "init",
        cwd=upstream,
    )
    return upstream


def test_install_symlinks_component(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    moonraker = _fake_moonraker_checkout(tmp_path)
    result = _run_install(repo_root, moonraker)
    assert result.returncode == 0, result.stderr
    link = moonraker / "moonraker" / "components" / "neuralyzer.py"
    assert link.is_symlink()
    assert link.resolve() == (repo_root / "neuralyzer.py").resolve()


def test_install_adds_exclude_line(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    moonraker = _fake_moonraker_checkout(tmp_path)
    _run_install(repo_root, moonraker)
    exclude = (moonraker / ".git" / "info" / "exclude").read_text()
    assert "moonraker/components/neuralyzer.py" in exclude


def test_install_is_idempotent(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    moonraker = _fake_moonraker_checkout(tmp_path)
    _run_install(repo_root, moonraker)
    result = _run_install(repo_root, moonraker)
    assert result.returncode == 0, result.stderr
    exclude = (moonraker / ".git" / "info" / "exclude").read_text()
    assert exclude.count("moonraker/components/neuralyzer.py") == 1
    link = moonraker / "moonraker" / "components" / "neuralyzer.py"
    assert link.is_symlink()


def test_install_via_stdin_clones_when_no_local_checkout(tmp_path):
    """Simulates `curl | bash`: install.sh is piped into bash on stdin, so
    it has no backing file to symlink neuralyzer.py from. It must fall
    back to cloning the repo (from NEURALYZER_REPO_URL, overridden here
    to a local stand-in repo instead of the real GitHub remote) into
    NEURALYZER_CLONE_DEST, and symlink from that clone instead."""
    repo_root = Path(__file__).resolve().parents[1]
    upstream = _fake_upstream_repo(tmp_path)
    moonraker = _fake_moonraker_checkout(tmp_path)
    clone_dest = tmp_path / "cloned-neuralyzer"

    env = dict(os.environ)
    env["NEURALYZER_REPO_URL"] = str(upstream)
    env["NEURALYZER_CLONE_DEST"] = str(clone_dest)

    result = subprocess.run(
        ["bash", "-s", "--", str(moonraker)],
        input=(repo_root / "install.sh").read_text(),
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr

    assert clone_dest.is_dir()
    link = moonraker / "moonraker" / "components" / "neuralyzer.py"
    assert link.is_symlink()
    assert link.resolve() == (clone_dest / "neuralyzer.py").resolve()


def test_install_via_stdin_is_idempotent(tmp_path):
    """A second curl | bash run must not re-clone or fail; git_ensure_clone
    recognizes the existing clone's origin matches and leaves it alone."""
    repo_root = Path(__file__).resolve().parents[1]
    upstream = _fake_upstream_repo(tmp_path)
    moonraker = _fake_moonraker_checkout(tmp_path)
    clone_dest = tmp_path / "cloned-neuralyzer"

    env = dict(os.environ)
    env["NEURALYZER_REPO_URL"] = str(upstream)
    env["NEURALYZER_CLONE_DEST"] = str(clone_dest)

    install_script = (repo_root / "install.sh").read_text()
    for _ in range(2):
        result = subprocess.run(
            ["bash", "-s", "--", str(moonraker)],
            input=install_script,
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, result.stderr

    exclude = (moonraker / ".git" / "info" / "exclude").read_text()
    assert exclude.count("moonraker/components/neuralyzer.py") == 1
