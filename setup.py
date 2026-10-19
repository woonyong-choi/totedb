"""wheel·sdist 빌드 때 C 공유 라이브러리를 만들어 totedb 패키지 안에 넣는다.

라이브러리는 ctypes로 여는 일반 공유 라이브러리라 Python ABI와 무관하다.
그래서 wheel 태그는 `py3-none-{플랫폼}` 하나다.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from setuptools import Distribution, setup
from setuptools.command.bdist_wheel import bdist_wheel
from setuptools.command.build_py import build_py

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tools.verify_benchmark import verify_library

ROOT = Path(__file__).resolve().parent
LIB_NAME = "libtotedb.dylib" if sys.platform == "darwin" else "libtotedb.so"


class BuildPyWithLibrary(build_py):
    """순수 Python 파일을 복사한 뒤 make로 만든 공유 라이브러리를 패키지에 더한다."""

    def run(self) -> None:
        super().run()
        build_dir = Path(self.build_lib).resolve().parent / "totedb-native-release"
        command = [
            "make",
            "-B",
            "-C",
            str(ROOT),
            f"BUILD_DIR={build_dir}",
            "BENCHMARK=0",
            "SANITIZE=",
            "all",
        ]
        if os.environ.get("CC"):
            command.append(f"CC={os.environ['CC']}")
        environment = os.environ.copy()
        for name in ("BENCHMARK", "MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES"):
            environment.pop(name, None)
        subprocess.run(command, check=True, env=environment)
        target = Path(self.build_lib) / "totedb" / LIB_NAME
        target.parent.mkdir(parents=True, exist_ok=True)
        source = build_dir / "lib" / LIB_NAME
        verify_library(source)
        shutil.copy2(source, target)
        verify_library(target)


class NativeDistribution(Distribution):
    """패키지에 네이티브 라이브러리가 있어 플랫폼별로 설치함을 setuptools에 알린다."""

    def has_ext_modules(self) -> bool:
        return True


class PlatformWheel(bdist_wheel):
    """Python 버전에 묶이지 않는 플랫폼 wheel로 태그한다."""

    def get_tag(self) -> tuple[str, str, str]:
        _, _, platform = super().get_tag()
        return "py3", "none", platform


setup(
    distclass=NativeDistribution,
    cmdclass={"build_py": BuildPyWithLibrary, "bdist_wheel": PlatformWheel},
)
