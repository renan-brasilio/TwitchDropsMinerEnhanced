from __future__ import annotations

# import an additional thing for proper PyInstaller freeze support
from multiprocessing import freeze_support


if __name__ == "__main__":
    freeze_support()
    import io
    import os
    import sys
    import signal
    import asyncio
    import logging
    import argparse
    import warnings
    import traceback
    try:
        import tkinter as tk
        from tkinter import messagebox
    except (ImportError, ModuleNotFoundError):
        tk = None
        messagebox = None
    from typing import NoReturn, TYPE_CHECKING

    import truststore
    truststore.inject_into_ssl()

    from translate import _
    from twitch import Twitch
    from settings import Settings
    from version import __version__
    from exceptions import CaptchaRequired
    from utils import lock_file, resource_path, set_root_icon
    from constants import LOGGING_LEVELS, SELF_PATH, FILE_FORMATTER, LOG_PATH, LOCK_PATH
    from api import APIServer

    if TYPE_CHECKING:
        from _typeshed import SupportsWrite

    warnings.simplefilter("default", ResourceWarning)

    # import tracemalloc
    # tracemalloc.start(3)

    if sys.version_info < (3, 10):
        raise RuntimeError("Python 3.10 or higher is required")

    # Suppress X11 Input Method registration on Linux to prevent
    # XWayland/Mutter lockups during heavy Tkinter layout updates.
    if sys.platform.startswith("linux") and "XMODIFIERS" not in os.environ:
        os.environ["XMODIFIERS"] = "@im=none"

    class Parser(argparse.ArgumentParser):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self._message: io.StringIO = io.StringIO()

        def _print_message(self, message: str, file: SupportsWrite[str] | None = None) -> None:
            self._message.write(message)
            # print(message, file=self._message)

        def exit(self, status: int = 0, message: str | None = None) -> NoReturn:
            try:
                super().exit(status, message)  # sys.exit(2)
            finally:
                val = self._message.getvalue()
                if messagebox is not None:
                    try:
                        messagebox.showerror("Argument Parser Error", val)
                    except Exception:
                        sys.stderr.write(val)
                else:
                    sys.stderr.write(val)

    class ParsedArgs(argparse.Namespace):
        _verbose: int
        _debug_ws: bool
        _debug_gql: bool
        log: bool
        tray: bool
        dump: bool

        # TODO: replace int with union of literal values once typeshed updates
        @property
        def logging_level(self) -> int:
            return LOGGING_LEVELS[min(self._verbose, 4)]

        @property
        def debug_ws(self) -> int:
            """
            If the debug flag is True, return DEBUG.
            If the main logging level is DEBUG, return INFO to avoid seeing raw messages.
            Otherwise, return NOTSET to inherit the global logging level.
            """
            if self._debug_ws:
                return logging.DEBUG
            elif self._verbose >= 4:
                return logging.INFO
            return logging.NOTSET

        @property
        def debug_gql(self) -> int:
            if self._debug_gql:
                return logging.DEBUG
            elif self._verbose >= 4:
                return logging.INFO
            return logging.NOTSET

    # handle input parameters
    # NOTE: parser output is shown via message box
    # we also need a dummy invisible window for the parser
    if tk is not None:
        root = tk.Tk()
        root.overrideredirect(True)
        root.withdraw()
        set_root_icon(root, resource_path("icons/pickaxe.ico"))
        root.update()
    else:
        root = None
    parser = Parser(
        SELF_PATH.name,
        description="A program that allows you to mine timed drops on Twitch.",
    )
    parser.add_argument("--version", action="version", version=f"v{__version__}")
    parser.add_argument("-v", dest="_verbose", action="count", default=0)
    parser.add_argument("--tray", action="store_true")
    parser.add_argument("--log", action="store_true")
    parser.add_argument("--dump", action="store_true")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"), help="API server bind host")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "23432")), help="API server port")
    parser.add_argument("--no-browser", action="store_true", help="Do not automatically open the browser")
    # undocumented debug args
    parser.add_argument(
        "--debug-ws", dest="_debug_ws", action="store_true", help=argparse.SUPPRESS
    )
    parser.add_argument(
        "--debug-gql", dest="_debug_gql", action="store_true", help=argparse.SUPPRESS
    )
    args = parser.parse_args(namespace=ParsedArgs())
    # load settings
    try:
        settings = Settings(args)
    except Exception:
        err_msg = f"There was an error while loading the settings file:\n\n{traceback.format_exc()}"
        if messagebox is not None:
            messagebox.showerror("Settings error", err_msg)
        else:
            sys.stderr.write(err_msg + "\n")
        sys.exit(4)
    # dummy window isn't needed anymore
    if root is not None:
        root.destroy()
    del root, parser

    # client run
    async def main():
        # set language
        try:
            _.set_language(settings.language)
        except ValueError:
            # this language doesn't exist - stick to English
            pass

        # handle logging stuff
        if settings.logging_level > logging.DEBUG:
            # redirect the root logger into a NullHandler, effectively ignoring all logging calls
            # that aren't ours. This always runs, unless the main logging level is DEBUG or lower.
            logging.getLogger().addHandler(logging.NullHandler())
        logger = logging.getLogger("TwitchDrops")
        logger.setLevel(settings.logging_level)
        if settings.log:
            handler = logging.FileHandler(LOG_PATH)
            handler.setFormatter(FILE_FORMATTER)
            logger.addHandler(handler)
        logging.getLogger("TwitchDrops.gql").setLevel(settings.debug_gql)
        logging.getLogger("TwitchDrops.websocket").setLevel(settings.debug_ws)

        exit_status = 0
        client = Twitch(settings)
        bind_host = getattr(args, "host", "127.0.0.1")
        bind_port = getattr(args, "port", getattr(settings, "api_port", 23432))
        api_server = APIServer(client, client.gui, host=bind_host, port=bind_port)
        await api_server.start()

        # Automatically open dashboard in default browser if local
        if not getattr(args, "no_browser", False) and not os.environ.get("DOCKER") and not os.environ.get("HEADLESS"):
            open_host = "127.0.0.1" if bind_host in ("0.0.0.0", "127.0.0.1") else bind_host
            try:
                import webbrowser
                webbrowser.open(f"http://{open_host}:{bind_port}")
            except Exception:
                pass

        loop = asyncio.get_running_loop()
        if sys.platform in ("linux", "darwin"):
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, lambda *_: client.gui.close())
        try:
            await client.run()
        except CaptchaRequired:
            exit_status = 1
            client.prevent_close()
            client.print(_("error", "captcha"))
        except Exception:
            exit_status = 1
            client.prevent_close()
            client.print("Fatal error encountered:\n")
            client.print(traceback.format_exc())
        finally:
            if sys.platform in ("linux", "darwin"):
                for sig in (signal.SIGINT, signal.SIGTERM):
                    loop.remove_signal_handler(sig)
            client.print(_("gui", "status", "exiting"))
            await client.shutdown()
            await api_server.stop()
        if not client.gui.close_requested:
            # user didn't request the closure
            client.gui.tray.change_icon("error")
            client.print(_("status", "terminated"))
            client.gui.status.update(_("gui", "status", "terminated"))
            # notify the user about the closure
            client.gui.grab_attention(sound=True)
        await client.gui.wait_until_closed()
        # save the application state
        # NOTE: we have to do it after wait_until_closed,
        # because the user can alter some settings between app termination and closing the window
        client.save(force=True)
        client.gui.stop()
        client.gui.close_window()
        sys.exit(exit_status)

    try:
        # use lock_file to check if we're not already running
        success, file = lock_file(LOCK_PATH)
        if not success:
            print("ERROR: Another instance of Twitch Drops Miner is already running (lock.file held).", file=sys.stderr)
            sys.exit(3)

        asyncio.run(main())
    finally:
        file.close()
