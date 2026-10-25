"""Compare the built motion with the preview Samuel approved (storyboard-r6/motion-r6a.mp4), every 1 s.
Local files only; nothing leaves the machine.

    uv run python diagram/compare.py                 # built frames from out/preview.mp4 (the HyperFrames render)
    uv run python diagram/compare.py --live          # built frames seeked in Chromium (no render needed)

Writes runs/r6/compare.png (each column one second: Samuel's frame above, the built frame below, the difference
under it) and runs/r6/compare.md (per frame: mean absolute grey difference, 0 to 255, and the share of pixels that
differ by more than 32 grey levels).
"""
from __future__ import annotations

import argparse
import subprocess
import os
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
SPEC = Path(os.environ.get("MOTION_SPEC", "motion-r6a.mp4"))  # the owner-approved preview; not in the repo; read only
OUT = HERE / "runs" / "r6"
W, H = 1440, 900
SW, SH = 360, 225  # one cell of the sheet


def grab(video: Path, sec: float, png: Path) -> None:
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{sec}", "-i", str(video), "-frames:v", "1",
                    "-vf", f"scale={W}:{H}", str(png)], check=True)


def gray(png: Path) -> bytes:
    return subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(png), "-vf", f"scale={W}:{H}", "-pix_fmt", "gray",
                           "-f", "rawvideo", "-"], capture_output=True, check=True).stdout


def live(times: list[float], d: Path) -> list[Path]:
    out = []
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": W, "height": H}, reduced_motion="no-preference")
        pg.goto((HERE / "index.html").as_uri())
        pg.evaluate("document.fonts.ready")
        for t in times:
            pg.evaluate(f"document.getAnimations().forEach(a => {{ a.pause(); a.currentTime = {t * 1000}; }})")
            p = d / f"b{t:05.1f}.png"
            pg.screenshot(path=str(p), animations="allow")
            out.append(p)
        b.close()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                str(SPEC)], capture_output=True, text=True, check=True).stdout)
    times = [float(s) for s in range(0, int(dur) + 1)]
    rows = ["# Built motion against Samuel's approved preview, every 1 s", "",
            f"Preview: `storyboard-r6/motion-r6a.mp4` ({dur:.2f} s). Built: "
            + ("frames seeked in Chromium at 1440 x 900." if a.live else "`out/preview.mp4` (HyperFrames render)."),
            "Mean: mean absolute grey difference (0 to 255). Changed: share of pixels that differ by more than 32.", "",
            "| Time | Mean | Changed |", "|---|---|---|"]
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        built = live(times, d) if a.live else []
        cells = []
        for i, sec in enumerate(times):
            s = d / f"s{sec:05.1f}.png"
            grab(SPEC, min(sec, dur - 0.02), s)
            b = built[i] if a.live else d / f"b{sec:05.1f}.png"
            if not a.live:
                grab(HERE / "out" / "preview.mp4", min(sec, dur - 0.02), b)
            ga, gb = gray(s), gray(b)
            diff = [abs(x - y) for x, y in zip(ga, gb)]
            mean = sum(diff) / len(diff)
            changed = sum(1 for x in diff if x > 32) / len(diff)
            rows.append(f"| {sec:.0f} s | {mean:.2f} | {changed * 100:.2f} % |")
            dpng = d / f"d{sec:05.1f}.png"
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(s), "-i", str(b), "-filter_complex",
                            f"[0]scale={W}:{H},format=gray[a];[1]scale={W}:{H},format=gray[b];"
                            "[a][b]blend=all_mode=difference,negate", str(dpng)], check=True)
            col = d / f"c{sec:05.1f}.png"
            label = f"{sec:.0f} s  mean {mean:.2f}  changed {changed * 100:.1f}%"
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(s), "-i", str(b), "-i", str(dpng),
                            "-filter_complex",
                            f"[0]scale={SW}:{SH}[a];[1]scale={SW}:{SH}[b];[2]scale={SW}:{SH}[c];[a][b][c]vstack=3,"
                            f"pad={SW}:{3 * SH + 24}:0:24:white,drawtext=text='{label}':x=6:y=5:fontsize=14:fontcolor=black",
                            str(col)], check=True)
            cells.append(col)
        per_row = 9
        lines = []
        for r in range(0, len(cells), per_row):
            chunk = cells[r:r + per_row]
            while len(chunk) < per_row:
                chunk = chunk + [chunk[-1]]
            line = d / f"row{r}.png"
            args = [x for c in chunk for x in ("-i", str(c))]
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args, "-filter_complex",
                            "".join(f"[{i}]" for i in range(len(chunk))) + f"hstack={len(chunk)}", str(line)], check=True)
            lines.append(line)
        args = [x for c in lines for x in ("-i", str(c))]
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args, "-filter_complex",
                        "".join(f"[{i}]" for i in range(len(lines))) + f"vstack={len(lines)}", "-map_metadata", "-1",
                        str(OUT / "compare.png")], check=True)
    rows += ["", "Each column of compare.png: Samuel's frame (top), the built frame (middle), the difference (bottom, "
             "dark where they differ)."]
    (OUT / "compare.md").write_text("\n".join(rows) + "\n")
    print("\n".join(rows))


if __name__ == "__main__":
    main()
