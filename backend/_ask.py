"""Temporary: start research runs the way the API does, print their ids."""
import asyncio, sys, uuid
from app.database.session import open_session
from app.modules.research.schemas import ResearchRunCreate
from app.modules.research.service import ResearchService

OWNER = uuid.UUID("e772fe75-6129-476a-8367-cd76b55c9406")
PROJECT = uuid.UUID("74e59721-bcf3-4fca-96e7-61341e4073bf")

async def main():
    async with open_session() as s:
        for q in sys.argv[1:]:
            run = await ResearchService(s).start_run(OWNER, ResearchRunCreate(project_id=PROJECT, query=q))
            print("RUN", run.id)

asyncio.run(main())
