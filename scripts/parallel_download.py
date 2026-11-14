"""Parallel HTTP Range downloader used for large Android SDK artifacts."""

from __future__ import annotations

import argparse
import math
import os
import shutil
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("output", type=Path)
    parser.add_argument("--size", type=int, required=True)
    parser.add_argument("--parts", type=int, default=8)
    args = parser.parse_args()
    output = args.output.resolve()
    work = output.parent / f"{output.stem}-parts"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    chunk = math.ceil(args.size / args.parts)
    # Windows accepts a case-insensitive environment block; normalizing avoids
    # duplicate keys inherited from mixed-case proxy variables.
    env = {str(key).lower(): str(value) for key, value in os.environ.items()}
    processes: list[tuple[int, subprocess.Popen, object, object, int]] = []
    for index in range(args.parts):
        start = index * chunk
        end = min(args.size - 1, (index + 1) * chunk - 1)
        stdout = (work / f"part-{index}.out.log").open("wb")
        stderr = (work / f"part-{index}.err.log").open("wb")
        process = subprocess.Popen(
            [
                "curl.exe" if os.name == "nt" else "curl",
                "--fail",
                "--location",
                "--retry",
                "5",
                "--retry-all-errors",
                "--range",
                f"{start}-{end}",
                "--output",
                f"part-{index}",
                args.url,
            ],
            cwd=work,
            env=env,
            stdout=stdout,
            stderr=stderr,
        )
        processes.append((index, process, stdout, stderr, end - start + 1))
    failed = False
    for index, process, stdout, stderr, expected in processes:
        code = process.wait()
        stdout.close()
        stderr.close()
        path = work / f"part-{index}"
        actual = path.stat().st_size if path.exists() else -1
        print(f"part {index}: exit={code}, bytes={actual}/{expected}", flush=True)
        failed |= code != 0 or actual != expected
    if failed:
        raise SystemExit(1)
    with output.open("wb") as target:
        for index in range(args.parts):
            with (work / f"part-{index}").open("rb") as source:
                shutil.copyfileobj(source, target, length=4 * 1024 * 1024)
    if output.stat().st_size != args.size:
        raise SystemExit(f"assembled size mismatch: {output.stat().st_size} != {args.size}")
    shutil.rmtree(work)
    print(f"Downloaded {output} ({args.size} bytes)")


if __name__ == "__main__":
    main()
