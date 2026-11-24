"""Collect and verify the standalone Windows OpenVINO execution provider."""

import argparse
from importlib import metadata
from pathlib import Path


OPENVINO_DISTRIBUTION = "onnxruntime-ep-openvino"
OPENVINO_PACKAGE = "onnxruntime_ep_openvino"
PLUGIN_DLL = "onnxruntime_providers_openvino_plugin.dll"
REQUIRED_DLLS = {
    PLUGIN_DLL,
    "openvino.dll",
    "openvino_intel_gpu_plugin.dll",
    "openvino_onnx_frontend.dll",
    "tbb12.dll",
}


def validate_onnxruntime_installation() -> None:
    """Guard against multiple distributions installing the onnxruntime package."""
    owners = metadata.packages_distributions().get("onnxruntime", [])
    if [owner.lower().replace("_", "-") for owner in owners] != ["onnxruntime-gpu"]:
        raise RuntimeError(f"Expected onnxruntime-gpu as the sole ONNX Runtime wheel, got {owners}")


def collect_openvino_files() -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Keep plugin dependencies next to the DLL, as shipped in the wheel."""
    dist = metadata.distribution(OPENVINO_DISTRIBUTION)
    binaries = []
    data = []
    for entry in dist.files or ():
        if entry.parts[0] != OPENVINO_PACKAGE or entry.suffix.lower() in {".py", ".pyc"}:
            continue
        source = Path(dist.locate_file(entry))
        if not source.is_file():
            raise RuntimeError(f"Missing file from {OPENVINO_DISTRIBUTION}: {source}")
        target = binaries if entry.suffix.lower() == ".dll" else data
        target.append((str(source), entry.parent.as_posix()))

    missing = REQUIRED_DLLS - {
        Path(source).name.lower()
        for source, directory in binaries
        if directory == OPENVINO_PACKAGE
    }
    if missing:
        raise RuntimeError(f"Missing required OpenVINO DLLs: {', '.join(sorted(missing))}")
    return binaries, data


def validate_openvino_bundle(app_dir: Path) -> None:
    internal = app_dir / "_internal"
    binaries, data = collect_openvino_files()
    for source, destination in binaries + data:
        bundled = internal / destination / Path(source).name
        if not bundled.is_file():
            raise RuntimeError(f"Missing OpenVINO file from Windows bundle: {bundled}")

    for distribution_name in ("onnxruntime-gpu", OPENVINO_DISTRIBUTION):
        dist = metadata.Distribution.discover(name=distribution_name, path=[str(internal)])
        if next(dist, None) is None:
            raise RuntimeError(f"Missing {distribution_name} metadata in the Windows bundle")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app_dir", type=Path)
    args = parser.parse_args()
    validate_onnxruntime_installation()
    validate_openvino_bundle(args.app_dir)
    print("Verified OpenVINO DLLs and package metadata for the Windows bundle")
