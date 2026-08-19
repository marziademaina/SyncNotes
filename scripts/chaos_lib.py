import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVERS = ["server-1", "server-2", "server-3"]
GATEWAY_URL = "http://localhost:8080"


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    print(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=REPO_ROOT, check=True, **kwargs)


def run_cli(*args: str, cwd: Path) -> str:
    """Invoke the real `client.cli` entry point (not a raw HTTP call) against
    the gateway, from `cwd` -- so a downloaded file and its .syncnotes.json
    sidecar land in a specific "user's" directory, the way an actual person
    running the CLI on their machine would see it.
    """
    import os

    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT / "client" / "src"))
    result = subprocess.run(
        [sys.executable, "-m", "client.cli", "--gateway", GATEWAY_URL, *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    print(f"$ python -m client.cli {' '.join(args)}  (in {cwd})\n{result.stdout}", end="")
    return result.stdout


def compose_up() -> None:
    run(["docker", "compose", "up", "--build", "-d"])


def compose_down() -> None:
    subprocess.run(["docker", "compose", "down", "-v"], cwd=REPO_ROOT, check=False)


def container(replica: str) -> str:
    return f"syncnotes-{replica}-1"


def container_network(replica: str) -> str:
    result = subprocess.run(
        [
            "docker",
            "inspect",
            container(replica),
            "--format",
            "{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{end}}",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _exec_python(replica: str, script: str) -> str | None:
    result = subprocess.run(
        ["docker", "exec", container(replica), "python", "-c", script],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def health(replica: str) -> dict | None:
    out = _exec_python(
        replica,
        "import urllib.request,json;"
        "print(json.dumps(json.loads(urllib.request.urlopen('http://localhost:8000/health').read())))",
    )
    return json.loads(out) if out else None


def get_file(replica: str, name: str) -> dict | None:
    out = _exec_python(
        replica,
        "import urllib.request,json;"
        f"print(json.dumps(json.loads(urllib.request.urlopen('http://localhost:8000/files/{name}').read())))",
    )
    return json.loads(out) if out else None


def trigger_reconcile(replica: str) -> dict | None:
    out = _exec_python(
        replica,
        "import urllib.request,json;"
        "req=urllib.request.Request('http://localhost:8000/internal/reconcile', method='POST');"
        "print(json.dumps(json.loads(urllib.request.urlopen(req).read())))",
    )
    return json.loads(out) if out else None


def corrupt_content(replica: str, name: str, bad_content: str) -> None:
    """Overwrite a file's content directly in a replica's SQLite DB, bypassing
    Raft entirely -- the storage-level bit-rot scenario reconciliation exists
    for. The stored hash is deliberately left untouched.
    """
    script = (
        "from server.db import get_session, FileRecord\n"
        "s = get_session()\n"
        f"r = s.get(FileRecord, {name!r})\n"
        f"r.content = {bad_content!r}\n"
        "s.commit()\n"
        "s.close()\n"
    )
    subprocess.run(["docker", "exec", container(replica), "python", "-c", script], check=True)


def gateway_post(name: str, content: str, base_version: int | None = None) -> dict:
    body = json.dumps({"content": content, "base_version": base_version}).encode()
    req = urllib.request.Request(
        f"{GATEWAY_URL}/files/{name}", data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def gateway_get(name: str) -> dict:
    with urllib.request.urlopen(f"{GATEWAY_URL}/files/{name}", timeout=10) as resp:
        return json.loads(resp.read())


def wait_until(predicate, timeout: float = 30.0, interval: float = 1.0, description: str = "condition") -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise TimeoutError(f"timed out waiting for: {description}")


def wait_for_cluster_quorum(timeout: float = 30.0) -> None:
    def _all_have_quorum() -> bool:
        return all((h := health(s)) and h.get("has_quorum") for s in SERVERS)

    wait_until(_all_have_quorum, timeout=timeout, description="all replicas report has_quorum=true")


def leader_replica_id() -> str:
    for name in SERVERS:
        h = health(name)
        if h and h.get("leader"):
            return h["leader"].split(":")[0]
    fail("no replica reports a leader")


def ok(message: str) -> None:
    print(f"  [OK] {message}")


def fail(message: str) -> None:
    print(f"  [FAIL] {message}")
    sys.exit(1)
