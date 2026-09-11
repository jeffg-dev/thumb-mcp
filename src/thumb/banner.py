"""Best-effort non-activating desktop control indicator in a separate UI process."""
from __future__ import annotations
import atexit
import os
import select
import subprocess
import sys
from contextlib import contextmanager

_process = None
status = 'not started'


def close():
    global _process
    if _process is not None:
        if _process.stdin:
            try:
                _process.stdin.close()  # EOF closes the helper's panel and exits.
            except OSError:
                pass
        try:
            _process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            _process.terminate()
        _process = None


atexit.register(close)


def _send(command):
    global _process, status
    if os.environ.get('THUMB_CONTROL_BANNER', '1') == '0':
        status = 'disabled'
        return
    try:
        if _process is None or _process.poll() is not None:
            if command == b'hide\n':
                return
            _process = subprocess.Popen(
                [sys.executable, '-m', 'thumb.banner'], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0,
            )
        _process.stdin.write(command)
        _process.stdin.flush()
        # A bounded acknowledgement ensures the banner is drawn before input.
        if not select.select([_process.stdout], [], [], 1.5)[0] or os.read(_process.stdout.fileno(), 1) != b'!':
            status = 'unavailable (UI helper did not acknowledge)'
            close()
            return
        status = 'visible' if command == b'show\n' else 'hidden'
    except (OSError, ValueError):
        status = 'unavailable (UI helper failed)'
        close()


@contextmanager
def controlling():
    _send(b'show\n')
    try:
        yield
    finally:
        _send(b'hide\n')


def _ui_main():
    # AppKit must run on the main thread. A separate helper avoids the MCP
    # worker thread and never becomes the active application or key window.
    import AppKit as A
    from Foundation import NSDate, NSRunLoop
    app = A.NSApplication.sharedApplication()
    app.setActivationPolicy_(A.NSApplicationActivationPolicyAccessory)
    screen = A.NSScreen.mainScreen().visibleFrame()
    width, height = 460, 48
    rect = A.NSMakeRect(screen.origin.x+(screen.size.width-width)/2,
                        screen.origin.y+screen.size.height-height-10, width, height)
    panel = A.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
        rect, A.NSWindowStyleMaskBorderless | A.NSWindowStyleMaskNonactivatingPanel,
        A.NSBackingStoreBuffered, False)
    panel.setLevel_(A.NSStatusWindowLevel)
    panel.setHidesOnDeactivate_(False)
    panel.setIgnoresMouseEvents_(True)
    panel.setCollectionBehavior_(A.NSWindowCollectionBehaviorCanJoinAllSpaces |
                                 A.NSWindowCollectionBehaviorFullScreenAuxiliary)
    panel.setBackgroundColor_(A.NSColor.colorWithCalibratedRed_green_blue_alpha_(.08,.18,.32,.97))
    label = A.NSTextField.labelWithString_('Thumb is controlling your computer — please wait')
    label.setFrame_(A.NSMakeRect(12, 13, width-24, 22))
    label.setFont_(A.NSFont.systemFontOfSize_weight_(14, A.NSFontWeightSemibold))
    label.setTextColor_(A.NSColor.whiteColor())
    label.setAlignment_(A.NSTextAlignmentCenter)
    panel.contentView().addSubview_(label)
    pending = b''
    try:
        while True:
            NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(.02))
            if select.select([sys.stdin], [], [], .02)[0]:
                data = os.read(sys.stdin.fileno(), 4096)
                if not data:
                    break
                pending += data
                while b'\n' in pending:
                    command, pending = pending.split(b'\n', 1)
                    if command == b'show':
                        panel.orderFrontRegardless()
                        panel.displayIfNeeded()
                    else:
                        panel.orderOut_(None)
                    NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(.02))
                    os.write(sys.stdout.fileno(), b'!')
    finally:
        panel.orderOut_(None)


if __name__ == '__main__':
    _ui_main()
