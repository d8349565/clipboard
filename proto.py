import time

from cliphist.store import ClipboardHistory
from cliphist.win_listener import ClipboardListener, HotkeyEvent


def main() -> None:
    history = ClipboardHistory(max_items=200)

    def on_event(evt):
        if isinstance(evt, HotkeyEvent):
            print(f"[hotkey] id={evt.hotkey_id}")
            return
        if history.add(evt):
            print(f"[{evt.item_type}] {evt.preview()}")

    listener = ClipboardListener(on_event=on_event)
    listener.start()
    print("Listening clipboard. Press Ctrl+C to exit.")

    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        listener.stop()


if __name__ == "__main__":
    main()
