import unittest

from backend.models.face_model import get_compute_mode, get_execution_provider


class ExecutionProviderTest(unittest.TestCase):
    def test_cuda_is_selected_when_available(self):
        provider = get_execution_provider(
            ["CUDAExecutionProvider", "CPUExecutionProvider"]
        )

        self.assertEqual(provider, "CUDAExecutionProvider")

    def test_cpu_is_selected_when_cuda_is_unavailable(self):
        provider = get_execution_provider(
            ["AzureExecutionProvider", "CPUExecutionProvider"]
        )

        self.assertEqual(provider, "CPUExecutionProvider")

    def test_compute_mode_matches_execution_provider(self):
        self.assertEqual(get_compute_mode("CUDAExecutionProvider"), "gpu")
        self.assertEqual(get_compute_mode("CPUExecutionProvider"), "cpu")
