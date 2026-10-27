"""`margin pinch-check`: run a fold page's pinch on a real Android phone and judge it by measurement.

A fold page moves one level for each pinch and keeps the paragraph under the fingers in place
(fold.page). Whether it does that on a phone was a question for a person until 2026-10-26, and a page
sent to a person to try by hand is the wrong way round: the phone can be driven over adb. This
serves the page to the phone only (`adb reverse`, the phone's own localhost), opens it in the phone's
Chromium browser, pinches it with two fingers through the browser's own synthetic gesture
(claude-human's `android_browser.pinch`, which feeds touch events into the browser's view, since a
phone with SELinux enforcing refuses the shell a write to its touch screen) behind the same checks as
a tap (the app has focus, no banner or call, nobody touched the phone for a few seconds), reads the
page after each pinch through the browser's debugging socket and judges four limits:

  - the level moves by exactly one, in the pinch's direction (none at the end of the scale);
  - the point of the paragraph that was under the fingers is less than 24 CSS pixels from where it was;
  - the browser's own zoom stays at 1, during the gesture and after it;
  - no frame takes longer than 50 ms from the gesture's start until the change has settled.

It records the screen meanwhile (screen captures twice a second, made into an mp4 with ffmpeg) and writes a table (markdown) and the readings (JSON) beside the
recording. Exit 0 only when every pinch passed.

    margin pinch-check page.html --serial HOST:PORT [--package com.brave.browser] [--out DIR] [--wait-unlock S]

The test never unlocks the phone (a PIN or a fingerprint is the person's own). With --wait-unlock it
waits, up to that many seconds, until the phone is awake, unlocked and untouched for a minute, so it
can run detached and start by itself when the person has the phone unlocked but is not using it.
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


#: the phone's wakefulness and its last input, as dumpsys power prints them
WAKEFULNESS = "dumpsys power | grep -E 'mWakefulness=|mLastUserActivityTime|ms ago'"
#: seconds nobody may have touched the phone before the test takes its screen
QUIET = 60.0
#: and after an attempt the person interrupted (another app came to the front), since they are
#: using the phone: five minutes untouched before the next attempt
QUIET_AFTER_LOSS = 300.0


def ready(power: str, locked: bool, idle: float | None, quiet: float = QUIET) -> bool:
    """Pure. Whether the phone can take the test now: awake, past its lock screen, and untouched for
    `quiet` seconds (`idle`, from claude_human.android.idle_s), so the browser does not take the
    screen from a person who is using the phone."""
    return "mWakefulness=Awake" in (power or "") and not locked and idle is not None and idle >= quiet


class Page:
    """The page in the phone's browser, through its debugging socket forwarded over adb. When the port
    stops answering (adb's connection to the phone dropped and came back, which removes its forwards),
    `eval` forwards it again and tries once more. `ab` is claude_human.android_browser."""

    def __init__(self, ab, serial: str, package: str, url: str, run_adb):
        self.ab, self.serial, self.package, self.url, self.run = ab, serial, package, url, run_adb
        self.port = ab.forward(serial, package, run_adb)

    def eval(self, js: str):
        try:
            return self.ab.evaluate(self.port, self.url, js)
        except self.ab.BrowserError as e:
            if "did not answer" not in str(e):
                raise
            self.ab.unforward(self.serial, self.port, self.run)
            self.port = self.ab.forward(self.serial, self.package, self.run)
            return self.ab.evaluate(self.port, self.url, js)

    def pinch(self, x: float, y: float, scale: float, speed: float) -> None:
        try:
            return self.ab.pinch(self.port, self.url, x, y, scale, speed=speed)
        except self.ab.BrowserError as e:
            if "did not answer" not in str(e):
                raise
            self.ab.unforward(self.serial, self.port, self.run)
            self.port = self.ab.forward(self.serial, self.package, self.run)
            return self.ab.pinch(self.port, self.url, x, y, scale, speed=speed)

    def close(self) -> None:
        self.ab.unforward(self.serial, self.port, self.run)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class PhoneLost(RuntimeError):
    """The phone left adb, or its browser's debugging socket went, in the middle of a run."""


def run(page: Path, serial: str, *, package: str = "com.brave.browser", out: Path | None = None,
        adb: str = "adb", wait_unlock: float = 0.0, attempts: int = 8, sleep=time.sleep,
        clock=time.monotonic) -> int:
    """Serve `page` to the phone, pinch it as plan() says, judge each pinch, and write the results.
    A phone that is lost in the middle (it left adb, its browser closed) is waited for again, within
    `wait_unlock`, and the run starts over, up to `attempts` times. Exit 0 when every pinch passed,
    1 when one failed, 2 when the test could not run."""
    from claude_human import android, android_browser as ab   # noqa: PLC0415
    out = Path(out or Path("/tmp/margin-pinch-check"))
    out.mkdir(parents=True, exist_ok=True)
    run_adb = android._runner(adb, None)
    end = clock() + wait_unlock

    def wait_ready(quiet: float = QUIET) -> bool:
        said = False
        while True:
            power = android._read(serial, run_adb, WAKEFULNESS) or ""
            if ready(power, android.locked(serial, run_adb), android.idle_s(power), quiet):
                break
            if clock() >= end:
                return False
            if not said:
                print(f"{time.strftime('%H:%M')} waiting for the phone to be awake, unlocked and untouched for "
                      f"{quiet:g} s", flush=True)
                said = True
            sleep(30)
        return True

    port = _free_port()
    handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(page.parent))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/{page.name}"
    rows: list[dict] | None = None
    try:
        for n in range(1, max(1, attempts) + 1):
            if not wait_ready(QUIET if n == 1 else QUIET_AFTER_LOSS):
                print("REFUSED: the phone is locked, asleep or off adb; it is never unlocked for the test "
                      "(--wait-unlock waits)")
                return 2
            print(f"{time.strftime('%H:%M')} the phone is awake, unlocked and idle: attempt {n}", flush=True)
            try:
                rows = _attempt(serial, package, url, port, out, adb, run_adb)
                break
            except (PhoneLost, ab.BrowserError) as e:
                print(f"{time.strftime('%H:%M')} attempt {n} lost the phone ({str(e)[:160]}); waiting for it again",
                      flush=True)
                rows = None
        if rows is None:
            print(f"REFUSED: the phone was lost in each of {attempts} attempts")
            return 2
    finally:
        server.shutdown()
    (out / "readings.json").write_text(json.dumps(rows, indent=1))
    (out / "results.md").write_text(table(rows) + "\n")
    print(table(rows))
    failed = [r for r in rows if r["why"]]
    print(f"\n{len(rows) - len(failed)} of {len(rows)} pinches passed; results in {out}")
    return 1 if failed or not rows else 0


#: how far a pinch moves the fingers (their end distance over their start) and how fast (CSS px/s)
SCALE = {"in": 0.4, "out": 2.5}
SPEED = {False: 800.0, True: 200.0}


class Frames:
    """The phone's screen, captured about twice a second while the test runs (screencap works where
    screenrecord may not write a file), and made into an mp4 with ffmpeg when it stops."""

    def __init__(self, adb: str, serial: str, folder: Path, gap: float = 0.5):
        self.adb, self.serial, self.folder, self.gap = adb, serial, folder, gap
        self.folder.mkdir(parents=True, exist_ok=True)
        self.stop_flag = threading.Event()
        self.n = 0
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self) -> None:
        while not self.stop_flag.is_set():
            r = subprocess.run([self.adb, "-s", self.serial, "exec-out", "screencap", "-p"], capture_output=True,
                               timeout=30)
            if r.returncode == 0 and r.stdout.startswith(b"\x89PNG"):
                self.n += 1
                (self.folder / f"{self.n:04d}.png").write_bytes(r.stdout)
            self.stop_flag.wait(self.gap)

    def stop(self, mp4: Path) -> bool:
        self.stop_flag.set()
        self.thread.join(timeout=40)
        if not self.n:
            return False
        r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", "2", "-i",
                            str(self.folder / "%04d.png"), "-vf", "scale=540:-2", "-pix_fmt", "yuv420p", str(mp4)],
                           capture_output=True)
        return r.returncode == 0 and mp4.exists()


def _attempt(serial: str, package: str, url: str, port: int, out: Path, adb: str, run_adb) -> list[dict]:
    """One run of the plan on the phone, each pinch made through the browser (android_browser.pinch)
    after the same checks as a tap (android.why_not: the browser has focus, no banner or call, nobody
    touched the phone for 3 s). Raises PhoneLost when the phone or its browser goes."""
    from claude_human import android, android_browser as ab   # noqa: PLC0415
    rows: list[dict] = []
    if run_adb(["-s", serial, "reverse", f"tcp:{port}", f"tcp:{port}"])[0] != 0:
        raise PhoneLost("adb could not reverse the port to the phone")
    frames = None
    try:
        if not ab.open_url(serial, package, url, run_adb):
            raise PhoneLost(f"could not open the page in {package}")
        page = Page(ab, serial, package, url, run_adb)
        try:
            for _ in range(30):
                try:
                    if page.eval("document.readyState") == "complete":
                        break
                except ab.BrowserError:
                    pass
                time.sleep(1)
            page.eval(LISTENER)
            page.eval("set('1')")
            view = page.eval("({w: innerWidth, h: innerHeight, dpr: devicePixelRatio})")
            x, y = view["w"] / 2, view["h"] * 0.45
            frames = Frames(adb, serial, out / "frames")
            last_place = None
            for place, direction, slow in plan():
                if place != last_place:
                    page.eval(f"scrollTo(0, {place} * (document.documentElement.scrollHeight - innerHeight))")
                    time.sleep(1)
                    last_place = place
                end = time.monotonic() + 120
                said = None
                while (why := android.why_not(serial, package, int(y * view["dpr"]), run_adb, idle=3.0,
                                              still=False)) is not None:
                    if "focus" in why or "locked" in why:
                        raise PhoneLost(f"not pinched: {why}")
                    if why != said:
                        print(f"{time.strftime('%H:%M')} waiting to pinch: {why}", flush=True)
                        said = why
                    if time.monotonic() > end:
                        break
                    time.sleep(1)
                if why is not None:
                    rows.append({"place": place, "direction": direction, "slow": slow, "reading": None,
                                 "why": [f"not pinched: {why}"]})
                    continue
                page.pinch(x, y, SCALE[direction], SPEED[slow])
                time.sleep(1.2)
                reading = page.eval(READ)
                rows.append({"place": place, "direction": direction, "slow": slow, "reading": reading,
                             "why": judge(reading, direction)})
        finally:
            page.close()
    finally:
        if frames is not None:
            frames.stop(out / "pinch.mp4")
        run_adb(["-s", serial, "reverse", "--remove", f"tcp:{port}"])
    return rows
