from threading import Lock


class PulseRegister:
    def __init__(self):
        self._pulses = 0
        self._lock = Lock()

    def add(self, pulses: int) -> None:
        if type(pulses) is not int or pulses < 0:
            raise ValueError('pulses must be a nonnegative integer')
        with self._lock:
            self._pulses += pulses

    def read(self) -> int:
        with self._lock:
            return self._pulses

    def read_clear(self) -> int:
        with self._lock:
            pulses = self._pulses
            self._pulses = 0
            return pulses
