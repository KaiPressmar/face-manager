"""Collect and verify the Windows CUDA 12 runtime used by the GPU installer."""

import argparse
from importlib import metadata
from pathlib import Path


CUDA_COMPONENTS = {
    "cuda_runtime": ("nvidia-cuda-runtime-cu12", {"cudart64_12.dll"}),
    "cublas": ("nvidia-cublas-cu12", {"cublas64_12.dll", "cublaslt64_12.dll"}),
    "cufft": ("nvidia-cufft-cu12", {"cufft64_11.dll"}),
    "cudnn": ("nvidia-cudnn-cu12", {"cudnn64_9.dll"}),
    "cuda_nvrtc": ("nvidia-cuda-nvrtc-cu12", set()),
}


def _check_required(component: str, filenames: set[str]) -> None:
    distribution_name, required = CUDA_COMPONENTS[component]
    missing = required - filenames
    if missing or not filenames:
        expected = ", ".join(sorted(missing)) if missing else "runtime DLLs"
        raise RuntimeError(f"Missing {expected} from {distribution_name}")


def collect_cuda_binaries() -> list[tuple[str, str]]:
    """Read DLLs from installed wheel records and retain ONNX Runtime's layout."""
    binaries = []
    for component, (distribution_name, _) in CUDA_COMPONENTS.items():
        dist = metadata.distribution(distribution_name)
        relative_dir = Path("nvidia", component, "bin")
        filenames = set()
        for entry in dist.files or ():
            if entry.parts[:-1] != relative_dir.parts or entry.suffix.lower() != ".dll":
                continue
            source = Path(dist.locate_file(entry))
            if not source.is_file():
                raise RuntimeError(f"Missing file from {distribution_name}: {source}")
            filenames.add(source.name.lower())
            binaries.append((str(source), relative_dir.as_posix()))
        _check_required(component, filenames)
    return binaries


def validate_cuda_bundle(app_dir: Path) -> None:
    """Fail packaging if the frozen application is missing its GPU runtime."""
    internal = app_dir / "_internal"
    if not (internal / "onnxruntime" / "capi" / "onnxruntime_providers_cuda.dll").is_file():
        raise RuntimeError("Missing the ONNX Runtime CUDA provider in the Windows GPU bundle")
    for source, destination in collect_cuda_binaries():
        bundled = internal / destination / Path(source).name
        if not bundled.is_file():
            raise RuntimeError(f"Missing NVIDIA runtime file from Windows GPU bundle: {bundled}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app_dir", type=Path)
    validate_cuda_bundle(parser.parse_args().app_dir)
    print("Verified ONNX Runtime and NVIDIA DLLs in the Windows GPU bundle")
