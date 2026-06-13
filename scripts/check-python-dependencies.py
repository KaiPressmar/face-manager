#!/usr/bin/env python3

import re
import subprocess
import sys
from importlib import metadata


INSIGHTFACE_ONNXRUNTIME_ERROR = re.compile(
    r"^insightface \S+ requires onnxruntime, which is not installed\.$"
)


def has_distribution(name):
    try:
        metadata.version(name)
    except metadata.PackageNotFoundError:
        return False
    return True


result = subprocess.run(
    [sys.executable, "-m", "pip", "check"],
    capture_output=True,
    text=True,
)
output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())

if result.returncode == 0:
    if output:
        print(output)
    raise SystemExit(0)

errors = [
    line
    for line in output.splitlines()
    if line.strip() and not line.startswith("WARNING:")
]
gpu_substitutes_for_cpu_package = (
    errors
    and all(INSIGHTFACE_ONNXRUNTIME_ERROR.match(line) for line in errors)
    and has_distribution("onnxruntime-gpu")
    and not has_distribution("onnxruntime")
)

if gpu_substitutes_for_cpu_package:
    import onnxruntime as ort

    if hasattr(ort, "preload_dlls"):
        ort.preload_dlls(directory="")
    if "CUDAExecutionProvider" in ort.get_available_providers():
        print(
            "Dependency check passed: onnxruntime-gpu satisfies InsightFace's "
            "onnxruntime API requirement."
        )
        raise SystemExit(0)

print(output, file=sys.stderr)
raise SystemExit(result.returncode)
