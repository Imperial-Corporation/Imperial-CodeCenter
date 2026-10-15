"""asyncio : tâches concurrentes, limite de parallélisme et délai maximal."""

import asyncio
import random


async def fetch(item: int, gate: asyncio.Semaphore) -> str:
    async with gate:  # au plus 3 tâches en même temps
        await asyncio.sleep(random.uniform(0.05, 0.2))
        if item == 4:
            raise ValueError("échec simulé")
        return f"résultat {item}"


async def main() -> None:
    gate = asyncio.Semaphore(3)
    tasks = [fetch(i, gate) for i in range(8)]

    # return_exceptions=True : une erreur n'annule pas les autres tâches.
    results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=5)

    for item, result in enumerate(results):
        print(item, "ERREUR" if isinstance(result, Exception) else result)


if __name__ == "__main__":
    asyncio.run(main())
