from __future__ import annotations

import argparse
import asyncio
import json

from app.agent.orchestrator import TravelOrchestrator


async def _main(message: str) -> None:
    orch = TravelOrchestrator()
    result = await orch.handle(message)
    print(result.message)
    print("\n--- trace ---")
    print(json.dumps(result.trace, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Travel agent CLI")
    parser.add_argument("message", nargs="?", default="去杭州玩2天，预算2000，喜欢博物馆")
    args = parser.parse_args()
    asyncio.run(_main(args.message))


if __name__ == "__main__":
    main()
