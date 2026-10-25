"""Make the stills of the diagram's final frame in Chromium, and the storyboard from the local preview render.
Local files only; nothing leaves the machine.

    uv run python diagram/stills.py

out/still.png        final frame, motion played to the end, the 1440 x 900 frame at 2x (blog and video)
out/reduced.png      the same frame under prefers-reduced-motion: reduce, with no animation created
out/still-390.png    final frame at phone width (390 px), whole page, 2x
out/reduced-390.png  the same at 390 px under reduced motion
out/still-744.png    final frame at the blog width: the 744 x 465 frame at 2x (LinkedIn shows article images 744 px wide)
out/reduced-744.png  the same at 744 x 465 under reduced motion
out/storyboard.png   one frame of out/preview.mp4 per move, cropped to the card, 360 px wide each (needs the render)
runs/contact-sheet.png  every 0.25 s of out/preview.mp4, 240 px wide each, 16 to a row (needs the render)
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
FRAME = (1440, 900)
PHONE = (390, 844)
BLOG = (744, 465)
SHEET_STEP_S = 0.25


def storyboard_times(browser) -> list[tuple[float, str]]:
    """One frame per stage, in seconds of preview.mp4, from the composition's own schedule: box 1 alone, 1 s after
    each hop's contact (the tag waits at the target's top edge), the climb, the grow, and the final frame. See
    README.md, "Timeline"."""
    page = browser.new_page(viewport={"width": FRAME[0], "height": FRAME[1]})
    page.goto((HERE / "index.html").as_uri())
    page.evaluate("document.fonts.ready")
    s = page.evaluate("window.PayerFinderDiagram.schedule(document.getElementById('pfd'))")
    page.close()
    m, h = s["m"], s["m"]["hops"]
    return [(0.5, "start: box 1 alone, lit, with its request"),
            (h[0]["at"] / 1000 + 1.0, "hop 1 done: box 2 lit; q, page waits at its top edge"),
            (h[1]["at"] / 1000 + 1.0, "hop 2 done: box 3 lit; q, customer_id, lim, off waits at its top edge"),
            (h[2]["at"] / 1000 + 1.0, "hop 3 done: box 4 lit; the same keys wait at its top edge"),
            (m["climb"]["s"] / 1000 + 0.4, "the answer climbs on the line, under the numbers"),
            (m["grow"]["s"] / 1000 + 0.4, "the grow: short text gone, boxes growing"),
            (s["end"] / 1000 + 1.0, "final frame: full text, the answer 200 OK · 10 rows in box 1")]


def shoot(browser, size: tuple[int, int], reduced: bool, path: Path) -> None:
    page = browser.new_page(viewport={"width": size[0], "height": size[1]}, device_scale_factor=2,
                            reduced_motion="reduce" if reduced else "no-preference")
    page.goto((HERE / "index.html").as_uri())
    page.evaluate("document.fonts.ready")
    if not reduced:
        page.evaluate("document.getAnimations().forEach(a => a.finish())")
    page.screenshot(path=str(path), animations="allow", full_page=size == PHONE)
    page.close()


def card_box(browser) -> tuple[int, int, int, int]:
    page = browser.new_page(viewport={"width": FRAME[0], "height": FRAME[1]}, reduced_motion="reduce")
    page.goto((HERE / "index.html").as_uri())
    page.evaluate("document.fonts.ready")
    r = page.evaluate("(() => { const r = document.querySelector('.pfd-card').getBoundingClientRect();"
                      " return [r.left, r.top, r.width, r.height]; })()")
    page.close()
    x, y, w, h = (round(v) for v in r)
    return x, y, w + (w % 2), h + (h % 2)


def storyboard(preview: Path, path: Path, box: tuple[int, int, int, int], times: list[tuple[float, str]]) -> None:
    x, y, w, h = box
    with tempfile.TemporaryDirectory() as t:
        frames = []
        for i, (sec, _) in enumerate(times):
            f = Path(t) / f"f{i}.png"
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{sec}", "-i", str(preview), "-frames:v", "1",
                            "-vf", f"crop={w}:{h}:{x}:{y},scale=360:-2:flags=lanczos", str(f)], check=True)
            frames.append(f)
        args = [a for f in frames for a in ("-i", str(f))]
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args, "-filter_complex",
                        "".join(f"[{i}]" for i in range(len(frames))) + f"hstack={len(frames)}",
                        "-map_metadata", "-1", str(path)], check=True)


def contact_sheet(preview: Path, path: Path) -> int:
    """Every SHEET_STEP_S seconds of the render, in time order, left to right and top to bottom."""
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                str(preview)], capture_output=True, text=True, check=True).stdout)
    n = int(dur / SHEET_STEP_S) + 1
    rows = -(-n // 16)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(preview), "-vf",
                    f"fps={1 / SHEET_STEP_S},scale=240:-2:flags=lanczos,tile=16x{rows}:padding=4:color=white",
                    "-frames:v", "1", "-map_metadata", "-1", str(path)], check=True)
    return n


def main() -> None:
    OUT.mkdir(exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        shoot(browser, FRAME, False, OUT / "still.png")
        shoot(browser, FRAME, True, OUT / "reduced.png")
        shoot(browser, PHONE, False, OUT / "still-390.png")
        shoot(browser, PHONE, True, OUT / "reduced-390.png")
        shoot(browser, BLOG, False, OUT / "still-744.png")
        shoot(browser, BLOG, True, OUT / "reduced-744.png")
        box = card_box(browser)
        times = storyboard_times(browser)
        browser.close()
    names = ["still.png", "reduced.png", "still-390.png", "reduced-390.png", "still-744.png", "reduced-744.png"]
    if (OUT / "preview.mp4").exists():
        storyboard(OUT / "preview.mp4", OUT / "storyboard.png", box, times)
        names.append("storyboard.png")
        for sec, what in times:
            print(f"storyboard {sec:5.2f} s  {what}")
        n = contact_sheet(OUT / "preview.mp4", HERE / "runs" / "contact-sheet.png")
        print(f"contact sheet: {n} frames, every {SHEET_STEP_S} s")
        names.append("../runs/contact-sheet.png")
    for name in names:
        print(OUT / name, (OUT / name).stat().st_size, "bytes")


if __name__ == "__main__":
    main()
