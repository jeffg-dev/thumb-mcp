"""Advisory circuit breaker for the observed stuck iPhone touch-input state."""
from dataclasses import dataclass

RECOVERY = (
    'Possible stuck iPhone touch input (THUMB-001): repeated taps had no visible effect. '
    'This is not proof of a defect: the target might be noninteractive. '
    'Further tap-like input is paused; no automatic retry. Ask the user to try one manual tap. '
    'If manual taps also fail: stop iPhone Mirroring, pick up and unlock the physical iPhone, '
    'interact with it, lock it and set it down (back on its charger if used), then resume Mirroring. '
    'Restarting only the Mac Mirroring app may not help. '
    'After the user confirms touch input works, call reconnect(input_recovered=true) '
    'and take a fresh snapshot. Do not reset this warning automatically. '
    'Screenshots and keyboard navigation remain available.'
)


@dataclass
class TapHealth:
    no_effect: int = 0

    @property
    def paused(self) -> bool:
        return self.no_effect >= 2

    def record(self, settle_status: str) -> str:
        if settle_status.startswith('no visible change'):
            self.no_effect += 1
            if self.paused:
                return RECOVERY
            return ('Tap had no visible effect. Check whether the target is interactive; '
                    'do not repeat it blindly. Two ineffective taps pause tap-like input '
                    'and provide physical-phone recovery instructions.')
        # A still-changing or blank frame is inconclusive. Only an observed
        # change followed by settling clears accumulated tap failures.
        if settle_status.startswith('settled'):
            self.reset()
        return ''

    def reset(self) -> None:
        self.no_effect = 0
