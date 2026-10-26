"""`margin pinch-check`: run a fold page's pinch on a real Android phone and judge it by measurement.

A fold page moves one level for each pinch and keeps the paragraph under the fingers in place
(fold.page). Whether it does that on a phone was a question for a person until 2026-10-26, and a page
sent to a person to try by hand is the wrong way round: the phone can be driven over adb. This
serves the page to the phone only (`adb reverse`, the phone's own localhost), opens it in the phone's
Chromium browser, pinches it with two real fingers (claude-human's `android_touch`, behind the same
checks as a tap: the app has focus, no banner or call, nobody touched the phone for a few seconds),
reads the page after each pinch through the browser's debugging socket (`android_browser`) and judges
four limits:

  - the level moves by exactly one, in the pinch's direction (none at the end of the scale);
  - the point of the paragraph that was under the fingers is less than 24 CSS pixels from where it was;
  - the browser's own zoom stays at 1, during the gesture and after it;
  - no frame takes longer than 50 ms from the gesture's start until the change has settled.

It records the screen meanwhile and writes a table (markdown) and the readings (JSON) beside the
recording. Exit 0 only when every pinch passed.

    margin pinch-check page.html --serial HOST:PORT [--package com.brave.browser] [--out DIR] [--wait-unlock S]

The test never unlocks the phone (a PIN or a fingerprint is the person's own). With --wait-unlock it
waits, up to that many seconds, until the phone is awake and unlocked, so it can run detached and
start by itself the next time the person picks the phone up.
"""
from __future__ import annotations

import http.server
import json
import socket
import subprocess
import threading
import time
from functools import partial
from pathlib import Path

#: the four limits
MAX_SHIFT_PX, MAX_FRAME_MS = 24.0, 50.0
#: the levels a fold page has
LOWEST, HIGHEST = 1, 5
#: where on the page each round of pinches happens, as a share of how far it scrolls
PLACES = (0.1, 0.5, 0.85)

#: what the page reports at the start of each two-finger gesture, and the frames it draws
LISTENER = r"""(() => {
  if (window.__pc) return 'ready';
  const pc = window.__pc = {starts: [], frames: [], maxScale: 1};
  let last = 0;
  const tick = t => { if (last) pc.frames.push([t, t - last]); last = t;
    if (pc.frames.length > 6000) pc.frames.splice(0, 3000); requestAnimationFrame(tick); };
  requestAnimationFrame(tick);
  const pick = (at, next, x, y) => {
    const a = at && ((next < 5 && at.closest('.u')) || at.closest('section'));
    if (!a) return null;
    if (!a.dataset.pc) a.dataset.pc = 'a' + Math.random().toString(36).slice(2);
    const r = a.getBoundingClientRect();
    return {id: a.dataset.pc, top: r.top, height: r.height,
            frac: r.height ? Math.min(1, Math.max(0, (y - r.top) / r.height)) : 0};
  };
  addEventListener('touchstart', e => {
    if (e.touches.length !== 2) return;
    const x = (e.touches[0].clientX + e.touches[1].clientX) / 2, y = (e.touches[0].clientY + e.touches[1].clientY) / 2;
    const at = document.elementFromPoint(x, y), level = +document.body.dataset.l;
    pc.maxScale = visualViewport.scale;
    pc.starts.push({t: performance.now(), x, y, level, pinches: window.pinches || 0,
                    up: pick(at, level - 1, x, y), down: pick(at, level + 1, x, y)});
  }, {capture: true, passive: true});
  visualViewport.addEventListener('resize', () => { pc.maxScale = Math.max(pc.maxScale, visualViewport.scale); });
  return 'ready';
})()"""

#: what happened since the last gesture began
READ = r"""(() => {
  const pc = window.__pc; if (!pc || !pc.starts.length) return null;
  const s = pc.starts[pc.starts.length - 1], level = +document.body.dataset.l;
  const which = level > s.level ? s.down : level < s.level ? s.up : null;
  let after = null;
  if (which) { const a = document.querySelector(`[data-pc="${which.id}"]`);
    if (a && a.offsetParent) { const r = a.getBoundingClientRect(); after = {top: r.top, height: r.height}; } }
  const gaps = pc.frames.filter(([t]) => t >= s.t && t <= s.t + 1500).map(([, d]) => d);
  return {before: s.level, level, y: s.y, anchor: which, after, scale: visualViewport.scale,
          max_scale: pc.maxScale, max_frame_ms: gaps.length ? Math.max(...gaps) : null,
          starts: pc.starts.length, pinches: window.pinches || 0};
})()"""


def expected(before: int, direction: str) -> int:
    """Pure. The level a pinch should end on: in folds one more, out opens one, within the scale."""
    return min(HIGHEST, max(LOWEST, before + (1 if direction == "in" else -1)))


def judge(reading: dict | None, direction: str) -> list[str]:
    """Pure. Why this pinch failed, one reason per limit it broke; [] when it passed."""
    if not reading:
        return ["the page reported no two-finger gesture"]
    why = []
    want = expected(reading["before"], direction)
    if reading["level"] != want:
        why.append(f"level {reading['before']} went to {reading['level']}, not {want}")
    if reading["level"] != reading["before"]:
        a, after = reading.get("anchor"), reading.get("after")
        if not a or not after:
            why.append("the paragraph under the fingers could not be found after the change")
        else:
            shift = abs(after["top"] + a["frac"] * after["height"] - reading["y"])
            if shift >= MAX_SHIFT_PX:
                why.append(f"the paragraph under the fingers moved {shift:.0f} px")
    zoom = max(reading.get("scale") or 1, reading.get("max_scale") or 1)
    if abs(zoom - 1) > 0.001:
        why.append(f"the browser zoomed to {zoom:.2f}")
    frame = reading.get("max_frame_ms")
    if frame is None:
        why.append("no frame was drawn while it changed")
    elif frame > MAX_FRAME_MS:
        why.append(f"a frame took {frame:.0f} ms")
    return why


def table(rows: list[dict]) -> str:
    """Pure. The results as a markdown table, one row a pinch."""
    out = ["| # | place | pinch | speed | level | shift px | zoom | slowest frame ms | result |",
           "|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        rd = r.get("reading") or {}
        a, after = rd.get("anchor"), rd.get("after")
        shift = (f"{abs(after['top'] + a['frac'] * after['height'] - rd['y']):.0f}" if a and after else "")
        zoom = max(rd.get("scale") or 1, rd.get("max_scale") or 1) if rd else ""
        frame = rd.get("max_frame_ms")
        out.append(f"| {i} | {r['place']:.0%} | {r['direction']} | {'slow' if r['slow'] else 'quick'} | "
                   f"{rd.get('before', '')} to {rd.get('level', '')} | {shift} | {zoom if zoom == '' else f'{zoom:.2f}'} | "
                   f"{'' if frame is None else f'{frame:.0f}'} | "
                   f"{'pass' if not r['why'] else 'FAIL ' + '; '.join(r['why'])} |")
    return "\n".join(out)


def plan() -> list[tuple[float, str, bool]]:
    """Pure. Each pinch to make, as (place, direction, slow): at each place two in and two out quick,
    then one in and one out slow; first of all one out at level 1, which must change nothing."""
    steps = [(PLACES[0], "out", False)]
    for place in PLACES:
        steps += [(place, "in", False), (place, "in", False), (place, "out", False), (place, "out", False),
                  (place, "in", True), (place, "out", True)]
    return steps


#: the phone's wakefulness, as dumpsys power prints it
WAKEFULNESS = "dumpsys power | grep -m1 mWakefulness="


def ready(power: str, locked: bool) -> bool:
    """Pure. Whether the phone can take the test now: awake and past its lock screen."""
    return "mWakefulness=Awake" in (power or "") and not locked


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run(page: Path, serial: str, *, package: str = "com.brave.browser", out: Path | None = None,
        adb: str = "adb", wait_unlock: float = 0.0, sleep=time.sleep, clock=time.monotonic) -> int:
    """Serve `page` to the phone, pinch it as plan() says, judge each pinch, and write the results.
    Exit 0 when every pinch passed, 1 when one failed, 2 when the test could not run."""
    from claude_human import android, android_browser as ab, android_touch as touch   # noqa: PLC0415
    out = Path(out or Path("/tmp/margin-pinch-check"))
    out.mkdir(parents=True, exist_ok=True)
    run_adb = android._runner(adb, None)
    end, said = clock() + wait_unlock, False
    while not ready(android._read(serial, run_adb, WAKEFULNESS) or "", android.locked(serial, run_adb)):
        if clock() >= end:
            print("REFUSED: the phone is locked or asleep; it is never unlocked for the test (--wait-unlock waits)")
            return 2
        if not said:
            print(f"{time.strftime('%H:%M')} waiting for the phone to be awake and unlocked", flush=True)
            said = True
        sleep(30)
    print(f"{time.strftime('%H:%M')} the phone is awake and unlocked: starting", flush=True)
    port = _free_port()
    handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(page.parent))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/{page.name}"
    rec, rows = None, []
    try:
        if run_adb(["-s", serial, "reverse", f"tcp:{port}", f"tcp:{port}"])[0] != 0:
            print("REFUSED: adb could not reverse the port to the phone")
            return 2
        if not ab.open_url(serial, package, url, run_adb):
            print(f"REFUSED: could not open the page in {package}")
            return 2
        with ab.forwarded(serial, package, run_adb) as dport:
            for _ in range(30):
                try:
                    if ab.evaluate(dport, url, "document.readyState") == "complete":
                        break
                except ab.BrowserError:
                    pass
                time.sleep(1)
            ab.evaluate(dport, url, LISTENER)
            ab.evaluate(dport, url, "set('1')")
            w, h = touch.screen_size(android._read(serial, run_adb, touch.WM_SIZE) or "") or (1080, 2400)
            rec = subprocess.Popen([adb, "-s", serial, "shell", "screenrecord", "--time-limit", "175",
                                    "/sdcard/margin-pinch-check.mp4"])
            last_place = None
            for place, direction, slow in plan():
                if place != last_place:
                    ab.evaluate(dport, url, f"scrollTo(0, {place} * (document.documentElement.scrollHeight - innerHeight))")
                    time.sleep(1)
                    last_place = place
                report = touch.checked_pinch(serial, package, w // 2, int(h * 0.45), direction, slow=slow,
                                             idle=3.0, wait=120.0, run=run_adb)
                if report["outcome"] != "success":
                    rows.append({"place": place, "direction": direction, "slow": slow, "reading": None,
                                 "why": [f"not pinched: {report['why']}"]})
                    continue
                time.sleep(1.2)
                reading = ab.evaluate(dport, url, READ)
                rows.append({"place": place, "direction": direction, "slow": slow, "reading": reading,
                             "why": judge(reading, direction)})
    finally:
        if rec is not None:
            subprocess.run([adb, "-s", serial, "shell", "pkill -INT screenrecord"], capture_output=True)
            try:
                rec.wait(timeout=20)
            except subprocess.TimeoutExpired:
                rec.kill()
            time.sleep(2)
            subprocess.run([adb, "-s", serial, "pull", "/sdcard/margin-pinch-check.mp4", str(out / "pinch.mp4")],
                           capture_output=True)
            subprocess.run([adb, "-s", serial, "shell", "rm -f /sdcard/margin-pinch-check.mp4"], capture_output=True)
        run_adb(["-s", serial, "reverse", "--remove", f"tcp:{port}"])
        server.shutdown()
    (out / "readings.json").write_text(json.dumps(rows, indent=1))
    (out / "results.md").write_text(table(rows) + "\n")
    print(table(rows))
    failed = [r for r in rows if r["why"]]
    print(f"\n{len(rows) - len(failed)} of {len(rows)} pinches passed; results in {out}")
    return 1 if failed or not rows else 0
