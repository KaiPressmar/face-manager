import runpy
import tempfile
import unittest
from importlib import metadata
from pathlib import Path
from unittest.mock import patch


MODULE = Path(__file__).resolve().parents[2] / "packaging/windows/cuda_runtime.py"
CUDA_WHEELS = {
    "nvidia-cuda-runtime-cu12": ("cuda_runtime", ("cudart64_12.dll",)),
    "nvidia-cublas-cu12": ("cublas", ("cublas64_12.dll", "cublasLt64_12.dll")),
    "nvidia-cufft-cu12": ("cufft", ("cufft64_11.dll",)),
    "nvidia-cudnn-cu12": ("cudnn", ("cudnn64_9.dll", "cudnn_ops64_9.dll")),
    "nvidia-cuda-nvrtc-cu12": ("cuda_nvrtc", ("nvrtc64_120_0.dll",)),
}


class CudaBundleTest(unittest.TestCase):
    def setUp(self):
        self.module = runpy.run_path(str(MODULE))
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for name, (component, filenames) in CUDA_WHEELS.items():
            wheel_dir = self.root / f"{name.replace('-', '_')}-1.0.dist-info"
            wheel_dir.mkdir()
            (wheel_dir / "METADATA").write_text(f"Name: {name}\nVersion: 1.0\n", encoding="utf-8")
            entries = [Path("nvidia", component, "bin", filename) for filename in filenames]
            for entry in entries:
                source = self.root / entry
                source.parent.mkdir(parents=True, exist_ok=True)
                source.touch()
            (wheel_dir / "RECORD").write_text(
                "".join(f"{entry.as_posix()},,\n" for entry in entries), encoding="utf-8",
            )
        distributions = {dist.name: dist for dist in metadata.distributions(path=[str(self.root)])}
        lookup = patch.object(metadata, "distribution", side_effect=distributions.__getitem__)
        lookup.start()
        self.addCleanup(lookup.stop)

    def test_collects_dlls_from_distributions_and_preserves_their_locations(self):
        collected = self.module["collect_cuda_binaries"]()
        expected = {
            (str(self.root / "nvidia" / component / "bin" / filename), f"nvidia/{component}/bin")
            for component, filenames in CUDA_WHEELS.values() for filename in filenames
        }
        self.assertEqual(set(collected), expected)

    def test_checks_native_provider_and_all_collected_cuda_files(self):
        app = self.root / "FaceManager"
        internal = app / "_internal"
        provider = internal / "onnxruntime" / "capi" / "onnxruntime_providers_cuda.dll"
        validate = self.module["validate_cuda_bundle"]
        with self.assertRaisesRegex(RuntimeError, "Missing the ONNX Runtime CUDA provider"):
            validate(app)
        provider.parent.mkdir(parents=True)
        provider.touch()
        for component, filenames in CUDA_WHEELS.values():
            folder = internal / "nvidia" / component / "bin"
            folder.mkdir(parents=True)
            for filename in filenames:
                (folder / filename).touch()
        validate(app)
        (internal / "nvidia/cudnn/bin/cudnn_ops64_9.dll").unlink()
        with self.assertRaisesRegex(RuntimeError, "cudnn_ops64_9"):
            validate(app)
