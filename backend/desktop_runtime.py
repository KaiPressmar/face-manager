"""Offline smoke check for installed desktop runtimes."""

import os
from pathlib import Path


def check_inference_runtime(bundle_root: Path) -> str:
    os.environ["NO_ALBUMENTATIONS_UPDATE"] = "1"

    import hnswlib
    import insightface
    import numpy as np
    import onnx
    import onnxruntime as ort
    import onnxruntime_ep_openvino as openvino
    from backend.models.face_model import CUDA_PROVIDER, CPU_PROVIDER, _find_intel_gpu

    assert insightface.__version__ == "0.7.3"
    plugin = Path(openvino.get_library_path()).resolve()
    assert plugin.is_relative_to(bundle_root) and plugin.is_file()
    assert CUDA_PROVIDER in ort.get_available_providers()
    _find_intel_gpu()  # Register the actual bundled plugin even on a CPU-only runner.

    index = hnswlib.Index(space="cosine", dim=2)
    index.init_index(max_elements=1)
    index.add_items(np.array([[0.0, 1.0]], dtype=np.float32))

    helper = onnx.helper
    graph = helper.make_graph(
        [helper.make_node("Identity", ["value"], ["result"])],
        "package-check",
        [helper.make_tensor_value_info("value", onnx.TensorProto.FLOAT, [1])],
        [helper.make_tensor_value_info("result", onnx.TensorProto.FLOAT, [1])],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 18)], ir_version=8)
    session = ort.InferenceSession(model.SerializeToString(), providers=[CPU_PROVIDER])
    value = np.array([42], dtype=np.float32)
    np.testing.assert_array_equal(session.run(None, {"value": value})[0], value)
    return ort.__version__
