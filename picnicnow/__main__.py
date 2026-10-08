"""Start de app:  python -m picnicnow [serve|sync|deals]"""

from __future__ import annotations

import argparse
import json
import logging


def main() -> None:
    parser = argparse.ArgumentParser(prog="picnicnow", description="Gespreksgestuurde boodschappenhulp voor Picnic")
    sub = parser.add_subparsers(dest="cmd")
    serve = sub.add_parser("serve", help="start de webapp (standaard)")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    sub.add_parser("sync", help="haal de Picnic-bestelgeschiedenis op")
    sub.add_parser("deals", help="ververs aanbiedingen en toon besparingskansen")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    from .core import App

    app = App()
    if args.cmd == "sync":
        from .history import sync_history

        print(json.dumps(sync_history(app.db, app.picnic), indent=2, ensure_ascii=False))
        return
    if args.cmd == "deals":
        from .deals import service

        print(json.dumps(service.refresh_deals(app.db, app.settings), indent=2, ensure_ascii=False))
        for o in service.opportunities(app.db, app.settings, service.default_checkjebon(app.settings)):
            print(f"- {o['product']['name']}: {o['store_name']} {o['deal']['title']} "
                  f"({o['deal'].get('label') or ''}) bespaart ~€{o['saving_cents'] / 100:.2f}")
        return

    import uvicorn

    from .server import create_app

    if app.picnic.demo and not app.db.get_pref("last_sync"):
        from .history import sync_history

        sync_history(app.db, app.picnic)  # demo: meteen voorbeeldhistorie
    host = getattr(args, "host", None) or app.settings.host
    port = getattr(args, "port", None) or app.settings.port
    print(f"\n  PicNicNow draait op http://{host}:{port}  (open dit op beide telefoons via je wifi-IP)\n")
    uvicorn.run(create_app(app), host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
