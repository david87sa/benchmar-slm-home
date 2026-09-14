
import importlib.util
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path


class FakeSysfs:
    """Build a fake Jetson sysfs tree in a temp dir and repoint gpu_metrics at it."""

    def __init__(self):
        self.root = tempfile.TemporaryDirectory()
        self.root_path = Path(self.root.name)

    def write(self, rel_path, content):
        path = self.root_path / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def cleanup(self):
        self.root.cleanup()


def load_gpu_metrics():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "gpu_metrics.py"
    spec = importlib.util.spec_from_file_location("gpu_metrics", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GpuMetricsJetsonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gpu = load_gpu_metrics()

    def setUp(self):
        self.fs = FakeSysfs()
        patcher = mock.patch.multiple(
            self.gpu,
            GPU_LOAD_PATH=str(self.fs.root_path / "sys/devices/gpu.0/load"),
            THERMAL_DIR=str(self.fs.root_path / "sys/devices/virtual/thermal"),
            DEVFREQ_DIR=str(self.fs.root_path / "sys/class/devfreq"),
            MEMINFO_PATH=str(self.fs.root_path / "proc/meminfo"),
            MODEL_PATH=str(self.fs.root_path / "proc/device-tree/model"),
            L4T_RELEASE_PATH=str(self.fs.root_path / "etc/nv_tegra_release"),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.fs.cleanup)
        # Emulate a Jetson board and no nvidia-smi.
        self.gpu.is_jetson = lambda: True
        self.gpu.nvidia_smi_available = lambda: False

    def _write_default_tree(self):
        self.fs.write("proc/meminfo", (
            "MemTotal:        8015564 kB\n"
            "MemFree:         3932044 kB\n"
            "MemAvailable:    4125812 kB\n"
            "Buffers:          123456 kB\n"
        ))
        self.fs.write("sys/devices/gpu.0/load", "375000\n")
        self.fs.write(
            "sys/devices/virtual/thermal/thermal_zone2/type", "GPU-therm\n"
        )
        self.fs.write(
            "sys/devices/virtual/thermal/thermal_zone2/temp", "51000\n"
        )
        self.fs.write("sys/class/devfreq/57000000.gpu/cur_freq", "1320000000\n")
        self.fs.write("proc/device-tree/model", "NVIDIA Orin Nano Developer Kit\n")
        self.fs.write(
            "etc/nv_tegra_release",
            "# R35 (release), REVISION: 4.1, GCID: 32394630, BOARD: t3636\n",
        )

    def test_jetson_full_tree(self):
        self._write_default_tree()
        metrics = self.gpu.get_gpu_metrics_jetson()
        self.assertEqual(metrics["backend"], "jetson")
        # 375000 / 10000 = 37.5
        self.assertEqual(metrics["util_gpu"], 37.5)
        # MemTotal 8015564 kB -> MB, used = total - available
        self.assertAlmostEqual(metrics["mem_total_mb"], 8015564 / 1024.0, places=1)
        self.assertAlmostEqual(
            metrics["mem_used_mb"], (8015564 - 4125812) / 1024.0, places=1
        )
        self.assertEqual(metrics["temp_gpu"], 51.0)
        # 1320000000 Hz = 1320 MHz
        self.assertEqual(metrics["clk_sm_mhz"], 1320)
        self.assertEqual(metrics["clk_mem_mhz"], None)
        self.assertEqual(metrics["name"], "NVIDIA Orin Nano Developer Kit")
        self.assertEqual(metrics["driver_version"], "L4T 35.4.1")
        self.assertIsNone(metrics["power_draw_w"])
        self.assertIsNone(metrics["fan_speed_pct"])

    def test_jetson_missing_sensors(self):
        self._write_default_tree()
        self.fs.root_path.joinpath("sys/devices/gpu.0/load").unlink()
        metrics = self.gpu.get_gpu_metrics_jetson()
        self.assertIsNone(metrics["util_gpu"])
        self.assertEqual(metrics["temp_gpu"], 51.0)

    def test_jetson_without_thermal_zones(self):
        self.fs.write("proc/meminfo", "MemTotal:        4000000 kB\n")
        self.fs.write("sys/devices/gpu.0/load", "1000000\n")
        metrics = self.gpu.get_gpu_metrics_jetson()
        self.assertEqual(metrics["util_gpu"], 100.0)
        self.assertIsNone(metrics["temp_gpu"])

    def test_jetson_ignores_non_gpu_thermal_zones(self):
        self._write_default_tree()
        # A non-GPU zone must not be picked for temp.
        self.fs.write(
            "sys/devices/virtual/thermal/thermal_zone0/type", "Tboard\n"
        )
        self.fs.write(
            "sys/devices/virtual/thermal/thermal_zone0/temp", "45000\n"
        )
        metrics = self.gpu.get_gpu_metrics_jetson()
        self.assertEqual(metrics["temp_gpu"], 51.0)

    def test_meminfo_missing_file(self):
        total, used = self.gpu._read_meminfo_mb()
        self.assertIsNone(total)
        self.assertIsNone(used)
class GpuMetricsNvidiaSmiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gpu = load_gpu_metrics()

    def test_parse_metrics_line(self):
        sample = (
            "NVIDIA GeForce RTX 4090, 555.42.02, 88, 10240, 24564, 72, "
            "335.20, 450.0, 35, 2520, 11008\n"
        )
        with mock.patch.object(self.gpu, "_run_nvidia_smi", return_value=sample):
            metrics = self.gpu.get_gpu_metrics_nvidia_smi()
        self.assertEqual(metrics["name"], "NVIDIA GeForce RTX 4090")
        self.assertEqual(metrics["util_gpu"], 88.0)
        self.assertEqual(metrics["mem_used_mb"], 10240.0)
        self.assertEqual(metrics["mem_total_mb"], 24564.0)
        self.assertEqual(metrics["temp_gpu"], 72.0)
        self.assertEqual(metrics["power_draw_w"], 335.2)
        self.assertEqual(metrics["power_limit_w"], 450.0)
        self.assertEqual(metrics["fan_speed_pct"], 35.0)
        self.assertEqual(metrics["clk_sm_mhz"], 2520.0)
        self.assertEqual(metrics["clk_mem_mhz"], 11008.0)

    def test_parse_unavailable_values(self):
        sample = (
            "NVIDIA GPU, 555.42.02, N/A, 1024, 8192, 50, [N/A], [N/A], [N/A], "
            "0, [N/A]\n"
        )
        with mock.patch.object(self.gpu, "_run_nvidia_smi", return_value=sample):
            metrics = self.gpu.get_gpu_metrics_nvidia_smi()
        self.assertIsNone(metrics["util_gpu"])
        self.assertIsNone(metrics["power_draw_w"])
        self.assertIsNone(metrics["fan_speed_pct"])
        self.assertEqual(metrics["clk_sm_mhz"], 0.0)

    def test_no_raw_output_returns_none(self):
        with mock.patch.object(self.gpu, "_run_nvidia_smi", return_value=None):
            self.assertIsNone(self.gpu.get_gpu_metrics_nvidia_smi())

    def test_get_gpu_processes_parses_comma_names(self):
        raw = (
            "1234, /usr/bin/python3.11, 1450\n"
            "5678, /root/llama.cpp/server, 5124\n"
        )
        with mock.patch.object(
            self.gpu, "_run_nvidia_smi", return_value=raw
        ), mock.patch.object(self.gpu, "nvidia_smi_available", return_value=True):
            processes = self.gpu.get_gpu_processes()
        self.assertEqual(len(processes), 2)
        self.assertEqual(processes[0]["pid"], "1234")
        self.assertEqual(processes[0]["name"], "python3.11")
        self.assertEqual(processes[1]["pid"], "5678")
        self.assertEqual(processes[1]["name"], "server")
        self.assertEqual(processes[1]["mem_mb"], 5124.0)

    def test_get_gpu_processes_empty_without_nvidia_smi(self):
        with mock.patch.object(self.gpu, "nvidia_smi_available", return_value=False):
            self.assertEqual(self.gpu.get_gpu_processes(), [])


class GpuMetricsFallbackTests(unittest.TestCase):
    """get_gpu_metrics() must fall back to Jetson sysfs when nvidia-smi
    exists but fails at runtime (the case on some JetPack builds)."""

    @classmethod
    def setUpClass(cls):
        cls.gpu = load_gpu_metrics()

    def test_falls_back_to_jetson_when_nvidia_smi_fails(self):
        with mock.patch.object(self.gpu, "nvidia_smi_available", return_value=True), \
                mock.patch.object(self.gpu, "get_gpu_metrics_nvidia_smi", return_value=None), \
                mock.patch.object(self.gpu, "is_jetson", return_value=True), \
                mock.patch.object(self.gpu, "get_gpu_metrics_jetson",
                                  return_value={"backend": "jetson", "util_gpu": 42.0}):
            metrics = self.gpu.get_gpu_metrics()
        self.assertEqual(metrics["backend"], "jetson")
        self.assertEqual(metrics["util_gpu"], 42.0)

    def test_no_backend_returns_none(self):
        with mock.patch.object(self.gpu, "nvidia_smi_available", return_value=False), \
                mock.patch.object(self.gpu, "is_jetson", return_value=False):
            self.assertIsNone(self.gpu.get_gpu_metrics())

    def test_get_gpu_sample_shape(self):
        with mock.patch.object(
            self.gpu,
            "get_gpu_metrics",
            return_value={
                "util_gpu": 12.5,
                "mem_used_mb": 2048.0,
                "mem_total_mb": 8192.0,
                "temp_gpu": 48.0,
            },
        ):
            sample = self.gpu.get_gpu_sample()
        self.assertEqual(sample["util"], 12.5)
        self.assertEqual(sample["mem_used"], 2048.0)
        self.assertEqual(sample["mem_total"], 8192.0)
        self.assertEqual(sample["temp"], 48.0)

    def test_get_gpu_sample_none_when_no_gpu(self):
        with mock.patch.object(self.gpu, "get_gpu_metrics", return_value=None):
            self.assertIsNone(self.gpu.get_gpu_sample())


if __name__ == "__main__":
    unittest.main()