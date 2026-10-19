import subprocess
import json
import os
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from setuptools import Distribution
from setuptools.command.build_py import build_py

from tools.verify_benchmark import verify_library

ROOT = Path(__file__).resolve().parents[1]
LIBRARY = "libtotedb.dylib" if sys.platform == "darwin" else "libtotedb.so"


class BenchmarkBuildTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        (ROOT / "build").mkdir(exist_ok=True)

    def test_benchmark_rejects_normal_directory(self) -> None:
        result = subprocess.run(
            ["make", "-n", "BENCHMARK=1", "BUILD_DIR=build", "all"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)

    def test_invalid_mode_is_rejected(self) -> None:
        result = subprocess.run(
            ["make", "-n", "BENCHMARK=yes", "all"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)

    def test_stripped_elf_dynamic_symbols_are_checked(self) -> None:
        details = "1: 0000000000000010 8 TLS LOCAL DEFAULT 12 tb_bench_tls\n2: 0000000000001000 8 FUNC GLOBAL DEFAULT 10 tb_bench_request\n"
        with (
            patch("tools.verify_benchmark.sys.platform", "linux"),
            patch(
                "tools.verify_benchmark.subprocess.run",
                side_effect=[
                    subprocess.CompletedProcess([], 0, stdout=""),
                    subprocess.CompletedProcess([], 0, stdout=details),
                ],
            ),
        ):
            with self.assertRaisesRegex(ValueError, "benchmark symbol mismatch"):
                verify_library(Path("stripped.so"))

    @unittest.skipIf(
        sys.platform == "darwin", "GNU strip ELF verification runs on Linux"
    )
    def test_actual_stripped_elf_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(
            dir=ROOT / "build", prefix="benchmark-stripped-"
        ) as directory:
            library = Path(directory) / "instrumented.so"
            subprocess.run(
                ["cc", "-shared", "-fPIC", "-x", "c", "-", "-o", str(library)],
                input="_Thread_local int tb_bench_tls; int tb_bench_request(void) { return tb_bench_tls; }",
                text=True,
                check=True,
            )
            subprocess.run(["strip", "--strip-all", str(library)], check=True)
            with self.assertRaisesRegex(ValueError, "benchmark symbol mismatch"):
                verify_library(library)

    def test_off_arguments_are_not_evaluated_or_parsed(self) -> None:
        source = r"""
        #include "src/benchmark.h"
        int main(void) {
            int counter = 0;
            TB_SCOPE(unknown_name, not_declared);
            TB_REQUEST_SCOPE(not_declared);
            TB_BEGIN(not_declared, ++counter);
            TB_END(not_declared);
            TB_IO(not_declared, ++counter, unknown_type, undefined_function());
            return counter;
        }
        """
        with tempfile.TemporaryDirectory(
            dir=ROOT / "build", prefix="benchmark-fixture-"
        ) as directory:
            binary = Path(directory) / "off"
            result = subprocess.run(
                ["cc", "-O0", "-Werror", "-I.", "-x", "c", "-", "-o", str(binary)],
                input=source,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(subprocess.run([str(binary)], check=False).returncode, 0)
            verify_library(binary)
            preprocessed = subprocess.run(
                ["cc", "-E", "-P", "-I.", "-x", "c", "-"],
                input=source,
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            self.assertNotIn("not_declared", preprocessed)
            self.assertNotIn("++counter", preprocessed)

    def test_custom_directory_mode_and_flags_rebuild(self) -> None:
        with tempfile.TemporaryDirectory(
            dir=ROOT / "build", prefix="benchmark-modes-"
        ) as directory:
            native = Path(directory)
            for mode in ("1", "0"):
                result = subprocess.run(
                    [
                        "make",
                        f"BUILD_DIR={native}",
                        f"BENCHMARK={mode}",
                        "SANITIZE=",
                        "counter-driver",
                        str(native / "storage/page_file.o"),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                verify_library(native / "lib" / LIBRARY, enabled=mode == "1")
                if mode == "0":
                    verify_library(native / "storage/page_file.o")
                output = native / f"smoke-{mode}"
                result = subprocess.run(
                    [
                        sys.executable,
                        "bench/counter_run.py",
                        "--build-dir",
                        str(native),
                        "--output",
                        str(output),
                        "--n",
                        "12",
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                for engine in ("tote", "sqlite-each", "sqlite-reuse"):
                    payload = json.loads((output / f"{engine}.json").read_text())
                    self.assertEqual(payload["completed"], 12)
                    self.assertEqual(payload["validated_persisted_counter"], 12)
                    expected_records = 36 if mode == "1" and engine == "tote" else 0
                    self.assertEqual(len(payload["records"]), expected_records)
                    for record in payload["records"]:
                        self.assertEqual(record["incomplete"], 0)
                        self.assertEqual(record["dropped"], 0)
                        self.assertIn(
                            record["operation"],
                            ("tote_begin", "tote_exec", "tote_commit"),
                        )
            header_change = subprocess.run(
                [
                    "make",
                    "-n",
                    "-W",
                    "include/totedb.h",
                    f"BUILD_DIR={native}",
                    "BENCHMARK=0",
                    "SANITIZE=",
                    "all",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn(f"-o {native / 'lib' / LIBRARY}", header_change.stdout)
            target = native / "storage/page_file.o"
            previous = target.stat().st_mtime_ns
            result = subprocess.run(
                [
                    "make",
                    f"BUILD_DIR={native}",
                    "BENCHMARK=0",
                    "SANITIZE=",
                    "CFLAGS=-Iinclude -DTEST_FLAG_CHANGE=1 -DTOTE_BENCHMARK",
                    str(target),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("-UTOTE_BENCHMARK", result.stdout)
            self.assertGreater(target.stat().st_mtime_ns, previous)
            verify_library(target)

    def test_packager_blocks_inherited_mode_and_rejects_contaminated_library(
        self,
    ) -> None:
        with patch("setuptools.setup"):
            definitions = runpy.run_path(str(ROOT / "setup.py"))
        command_type = definitions["BuildPyWithLibrary"]
        with tempfile.TemporaryDirectory(
            dir=ROOT / "build", prefix="benchmark-package-"
        ) as directory:
            build_lib = Path(directory) / "python"
            native = Path(directory) / "totedb-native-release" / "lib" / LIBRARY
            native.parent.mkdir(parents=True)
            fixture = subprocess.run(
                ["cc", "-shared", "-fPIC", "-x", "c", "-", "-o", str(native)],
                input="_Thread_local int tb_bench_tls; int tb_bench_bad(void) { return tb_bench_tls; }",
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(fixture.returncode, 0, fixture.stderr)
            command = command_type(Distribution())
            command.build_lib = str(build_lib)
            with (
                patch.object(build_py, "run"),
                patch.dict(
                    os.environ,
                    {
                        "BENCHMARK": "1",
                        "MAKEFLAGS": "-e BENCHMARK=1",
                        "MFLAGS": "-e",
                        "MAKEOVERRIDES": "BENCHMARK=1",
                    },
                ),
                patch("subprocess.run", wraps=subprocess.run) as run,
            ):

                def skip_make(
                    args: list[str], **kwargs: object
                ) -> subprocess.CompletedProcess:
                    if args[0] == "make":
                        self.assertIn("BENCHMARK=0", args)
                        self.assertIn("-B", args)
                        self.assertIn("SANITIZE=", args)
                        self.assertNotIn("MAKEFLAGS", kwargs["env"])
                        self.assertNotIn("BENCHMARK", kwargs["env"])
                        return subprocess.CompletedProcess(args, 0)
                    return run._mock_wraps(args, **kwargs)

                run.side_effect = skip_make
                with self.assertRaisesRegex(ValueError, "benchmark symbol mismatch"):
                    command.run()
            self.assertFalse((build_lib / "totedb" / LIBRARY).exists())
            with (
                patch.object(build_py, "run"),
                patch.dict(
                    os.environ,
                    {
                        "BENCHMARK": "1",
                        "MAKEFLAGS": "-e BENCHMARK=1",
                        "MFLAGS": "-e",
                        "MAKEOVERRIDES": "BENCHMARK=1",
                    },
                ),
            ):
                command.run()
            verify_library(build_lib / "totedb" / LIBRARY)
