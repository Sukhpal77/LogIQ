import pytest
from app.context.graph import add_dependency
from app.db.models import Service, Dependency
from app.models.schemas import DependencyEdge


@pytest.mark.asyncio
async def test_registering_existing_edge_preserves_database_kind():
    source = Service(id=1, name="demo-shop-api", kind="app")
    target = Service(id=2, name="demo-shop-db", kind="db")
    existing = Dependency(id=3, source_id=1, target_id=2, relation="reads")
    class Session:
        responses = iter([source, target, existing])
        async def execute(self, _):
            value = next(self.responses)
            class Result:
                def scalar_one_or_none(self):
                    return value
            return Result()
    result = await add_dependency(Session(), DependencyEdge(source=source.name, target=target.name, relation="reads"))
    assert result is existing
    assert target.kind == "db"
