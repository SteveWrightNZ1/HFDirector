from __future__ import annotations

import argparse
import atexit
import logging

from .catalogue import Catalogue
from .config import Config
from .db import Database
from .director import Director, Scheduler
from .qsstv import QSSTVClient
from .web import create_app
from .sources.metservice import MetServiceSource
from .sources.ecmwf import ECMWFOpenChartsSource
from .sources.manager import SourceManager
from .modems import ManagedQSSTVClient, ModemSupervisor


def components(config: Config):
    db = Database(config.database)
    db.initialise()
    catalogue = Catalogue(db, config.asset_root)
    qsstv = QSSTVClient(config.qsstv_url)
    director = Director(db, catalogue, qsstv, config.timezone)
    return db, catalogue, qsstv, director


def main() -> None:
    parser = argparse.ArgumentParser(description="HFDirector station and broadcast controller")
    parser.add_argument("command", nargs="?", choices=("serve", "init", "import"), default="serve")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)
    config = Config.from_env()
    _, catalogue, _, director = components(config)
    if args.command == "init":
        print(f"Initialised {config.database}")
        return
    if args.command == "import":
        print(catalogue.ingest_tree(config.import_root))
        return
    supervisor = ModemSupervisor(config.root.parent, config.qsstv_url)
    supervisor.start()
    atexit.register(supervisor.stop)
    app = create_app(config, ManagedQSSTVClient(config.qsstv_url, supervisor), supervisor)
    director = app.config["DIRECTOR"]
    sources = SourceManager(
        MetServiceSource(config, catalogue), ECMWFOpenChartsSource(catalogue)
    )
    scheduler = Scheduler(director, config.poll_seconds, sources, config.fetch_seconds)
    scheduler.start()
    app.run(host=config.bind_host, port=config.bind_port, debug=args.debug, use_reloader=False)


if __name__ == "__main__":
    main()
