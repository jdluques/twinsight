"""CLI de TwinSight: arrancar el servidor o sembrar la demo."""

from __future__ import annotations

import argparse
import asyncio

from ..config import get_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="twinsight", description="Servidor TwinSight")
    sub = parser.add_subparsers(dest="cmd")

    run = sub.add_parser("run", help="Arranca el servidor (por defecto)")
    run.add_argument("--host", default=None)
    run.add_argument("--port", type=int, default=None)
    run.add_argument("--reload", action="store_true")

    sub.add_parser("seed-demo", help="Siembra el proyecto de demo 'Aulas PI3'")

    args = parser.parse_args(argv)
    settings = get_settings()

    if args.cmd == "seed-demo":
        from ..modelgraph import get_model_graph
        from ..persistence.db import get_sessionmaker, init_db
        from .seed import seed_demo

        async def _seed():
            await init_db()
            g = get_model_graph()
            await g.connect()
            pid = await seed_demo(get_sessionmaker(), g)
            await g.close()
            print(f"Demo sembrada: proyecto {pid}")

        asyncio.run(_seed())
        return 0

    # run (por defecto)
    import uvicorn

    uvicorn.run(
        "twinsight.app.main:app",
        host=getattr(args, "host", None) or settings.host,
        port=getattr(args, "port", None) or settings.port,
        reload=getattr(args, "reload", False),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
