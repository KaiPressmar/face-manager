"""Record and verify the contents of the Ubuntu release package."""

import argparse
from importlib import metadata
import json
from pathlib import Path
import subprocess
import tarfile


PACKAGE = "face-manager"
TARGET = "Ubuntu 26.04 amd64"
PREFIX = "opt/face-manager"
MANIFEST = f"{PREFIX}/bundle-manifest.json"
MAX_RELEASE_BYTES = 2 * 1024**3
EXPECTED_DISTRIBUTIONS = {
    "onnxruntime-gpu": "1.26.0",
    "onnxruntime-ep-openvino": "1.8.1",
    "insightface": "0.7.3",
    "nvidia-cuda-runtime-cu12": "12.9.79",
    "nvidia-cuda-nvrtc-cu12": "12.9.86",
    "nvidia-nvjitlink-cu12": "12.9.86",
    "nvidia-cublas-cu12": "12.9.1.4",
    "nvidia-cufft-cu12": "11.4.1.4",
    "nvidia-curand-cu12": "10.3.10.19",
    "nvidia-cudnn-cu12": "9.21.0.82",
}
REQUIRED_FILES = {
    "usr/bin/face-manager",
    "usr/share/applications/face-manager.desktop",
    "usr/share/icons/hicolor/scalable/apps/face-manager.svg",
    f"{PREFIX}/BUILD_VARIANT",
    f"{PREFIX}/CHANGELOG.md",
    f"{PREFIX}/VERSION",
    f"{PREFIX}/launch.py",
    f"{PREFIX}/backend/desktop_main.py",
    f"{PREFIX}/frontend/dist/index.html",
    f"{PREFIX}/vendor/onnxruntime/capi/libonnxruntime_providers_cuda.so",
    f"{PREFIX}/vendor/onnxruntime/capi/libonnxruntime_providers_shared.so",
    MANIFEST,
}
NVIDIA_LIBRARIES = {
    "cuda_runtime/lib/libcudart.so.12",
    "cuda_nvrtc/lib/libnvrtc.so.12",
    "nvjitlink/lib/libnvJitLink.so.12",
    "cublas/lib/libcublas.so.12",
    "cublas/lib/libcublasLt.so.12",
    "cufft/lib/libcufft.so.11",
    "curand/lib/libcurand.so.10",
    "cudnn/lib/libcudnn.so.9",
}
REQUIRED_FILES |= {f"{PREFIX}/vendor/nvidia/{name}" for name in NVIDIA_LIBRARIES}
RUNTIME_DEPENDENCIES = (
    "python3.14",
    "python3-gi",
    "python3-gi-cairo",
    "python3-tk",
    "gir1.2-gtk-3.0",
    "gir1.2-webkit2-4.1",
    "libgl1",
    "libgomp1",
    "libstdc++6",
    "ocl-icd-libopencl1",
    "xdg-utils",
)


def normalize_name(name):
    return name.lower().replace("_", "-")


def check_distributions(distributions):
    for name, version in EXPECTED_DISTRIBUTIONS.items():
        if distributions.get(name) != version:
            raise SystemExit(f"Expected {name} {version} in private dependencies")
    if {"onnxruntime", "onnxruntime-openvino"}.intersection(distributions):
        raise SystemExit("Found a second ONNX Runtime distribution")


def manifest(root, version):
    distributions = {
        normalize_name(item.metadata["Name"]): item.version
        for item in metadata.distributions(path=[str(root / "vendor")])
    }
    check_distributions(distributions)
    data = {"version": version, "target": TARGET, "python_abi": "3.14", "distributions": distributions}
    (root / "bundle-manifest.json").write_text(json.dumps(data, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def deb_field(artifact, field):
    return subprocess.check_output(["dpkg-deb", "-f", artifact, field], text=True).strip()


def check(artifact, version):
    size = artifact.stat().st_size
    if not 0 < size < MAX_RELEASE_BYTES:
        raise SystemExit(f"Package size {size} is outside GitHub release asset limits")
    if deb_field(artifact, "Package") != PACKAGE or deb_field(artifact, "Architecture") != "amd64":
        raise SystemExit("Unexpected Linux package name or architecture")
    if version is not None and deb_field(artifact, "Version") != version:
        raise SystemExit("Linux package does not match VERSION")
    depends = deb_field(artifact, "Depends").split(", ")
    if not all(name in depends for name in RUNTIME_DEPENDENCIES):
        raise SystemExit("Missing Ubuntu runtime dependencies")

    files = set()
    values = {}
    with subprocess.Popen(["dpkg-deb", "--fsys-tarfile", artifact], stdout=subprocess.PIPE) as process:
        with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
            for member in archive:
                name = member.name.removeprefix("./")
                if member.isfile() or member.issym():
                    files.add(name)
                if name in {MANIFEST, f"{PREFIX}/BUILD_VARIANT", f"{PREFIX}/VERSION"}:
                    values[name] = archive.extractfile(member).read().decode("utf-8").strip()
                if name == "usr/bin/face-manager" and not member.mode & 0o111:
                    raise SystemExit("The desktop launcher is not executable")
                if name.endswith(".onnx") or "/.insightface/" in name:
                    raise SystemExit(f"An ONNX model was bundled: {name}")
                if Path(name).name.startswith(("libcuda.so", "libnvidia-ml.so")):
                    raise SystemExit(f"A host driver library was bundled: {name}")
        if process.wait():
            raise SystemExit("Could not read the Debian payload")

    missing = REQUIRED_FILES - files
    if missing:
        raise SystemExit("Missing package files: " + ", ".join(sorted(missing)))
    if not any(
        name.startswith(f"{PREFIX}/vendor/onnxruntime_ep_openvino/") and name.endswith(".so")
        for name in files
    ):
        raise SystemExit("OpenVINO execution-provider library is missing")
    if values[f"{PREFIX}/BUILD_VARIANT"] != "linux":
        raise SystemExit("Unexpected desktop build variant")
    data = json.loads(values[MANIFEST])
    if data["target"] != TARGET or data["python_abi"] != "3.14":
        raise SystemExit("Unexpected target in the Linux manifest")
    if data["version"] != values[f"{PREFIX}/VERSION"] or data["version"] != deb_field(artifact, "Version"):
        raise SystemExit("Manifest version does not match the package")
    check_distributions(data["distributions"])
    for name, expected in EXPECTED_DISTRIBUTIONS.items():
        wheel_record = f"{PREFIX}/vendor/{name.replace('-', '_')}-{expected}.dist-info/METADATA"
        if wheel_record not in files:
            raise SystemExit(f"Missing wheel metadata: {wheel_record}")
    print(f"Verified Ubuntu package metadata, wheel layout and {size:,}-byte release size.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["manifest", "check", "depends"])
    parser.add_argument("path", nargs="?", type=Path)
    parser.add_argument("version", nargs="?")
    args = parser.parse_args()
    if args.action == "depends":
        print(", ".join(RUNTIME_DEPENDENCIES))
    elif args.path is None or (args.action == "manifest" and args.version is None):
        parser.error("a path is required, and manifest also requires a version")
    elif args.action == "manifest":
        manifest(args.path, args.version)
    else:
        check(args.path, args.version)
