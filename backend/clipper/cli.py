"""Command line: ``clipper serve`` (API + dashboard) and ``clipper worker`` (independent render worker)."""

from __future__ import annotations

import argparse
import asyncio
import signal

from clipper.core.config import get_settings


def serve(args: argparse.Namespace) -> None:
    import uvicorn

    s = get_settings()
    uvicorn.run("clipper.main:create_app", factory=True, host=args.host or s.host, port=args.port or s.port,
                log_level=s.log_level.lower(), reload=False,
                # uvloop forks in-process to spawn subprocesses, which can deadlock once ASR/OpenMP threads
                # exist; the stdlib loop uses fork_exec (exec happens in C), which is safe with threads.
                loop="asyncio")


def worker(args: argparse.Namespace) -> None:
    """Standalone worker process consuming jobs from Redis (CLIPPER_QUEUE_BACKEND=redis)."""
    from clipper.main import bootstrap

    s = get_settings()
    if s.queue_backend != "redis":
        raise SystemExit("workers require CLIPPER_QUEUE_BACKEND=redis")

    async def main() -> None:
        ctx = await bootstrap(s, run_workers=False, resume=False)
        await ctx["queue"].start(args.types, {t: args.concurrency for t in args.types})
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        print(f"worker consuming {args.types} x{args.concurrency}")
        await stop.wait()
        await ctx["queue"].stop()

    asyncio.run(main())


def main() -> None:
    p = argparse.ArgumentParser(prog="clipper")
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("serve", help="run API + dashboard")
    sp.add_argument("--host")
    sp.add_argument("--port", type=int)
    sp.set_defaults(fn=serve)
    wp = sub.add_parser("worker", help="run an independent job worker (redis backend)")
    wp.add_argument("--types", nargs="+", default=["render"])
    wp.add_argument("--concurrency", type=int, default=1)
    wp.set_defaults(fn=worker)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
