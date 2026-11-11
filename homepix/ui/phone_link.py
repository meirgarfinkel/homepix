"""Answering paired phones while HomePix runs (and while a phone is being paired): starts the server
when there's a phone to answer, stops it when there's none, and passes on (on the window's thread) when
a phone pairs or a photo arrives."""

from PySide6.QtCore import QObject, Signal

from ..core import phones


class PhoneLink(QObject):
    paired = Signal(str)  # a phone's name, when it has paired
    arrived = Signal(str)  # a phone's name, when one of its files is in place

    def __init__(self, parent=None):
        super().__init__(parent)
        self.server = None
        self.problem = ""  # why it can't answer phones, when it can't

    @property
    def port(self) -> int | None:
        return self.server.port if self.server is not None else None

    def _event(self, kind: str, phone):  # (from the server's thread: the signals carry it over)
        if kind == "paired":
            self.paired.emit(phone.name)
        elif kind == "arrived":
            self.arrived.emit(phone.name)

    def refresh(self) -> bool:
        """Answer phones only when one is paired or being paired. Whether it's answering now."""
        want = bool(phones.load_phones()) or phones.pairing() is not None
        if want and self.server is None:
            from ..core.phone_server import Server

            server = Server(self._event)
            try:
                server.start()
            except Exception as e:  # (no network, say)
                self.problem = f"HomePix couldn't start listening for phones: {e}"
                return False
            self.server, self.problem = server, ""
        elif not want and self.server is not None:
            self.stop()
        return self.server is not None

    def stop(self):
        if self.server is not None:
            self.server.stop()
            self.server = None
