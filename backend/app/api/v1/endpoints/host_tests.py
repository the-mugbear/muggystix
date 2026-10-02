"""One host-test surface with the application's two authentication adapters."""
from dataclasses import dataclass

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import check_agent_rate_limit, get_current_project, require_project_role
from app.api.v1.endpoints.agent_common import load_agent_session, require_project_host
from app.api.deps import get_current_user
from app.db.models_agent import Agent
from app.db.models_auth import User
from app.db.models_project import Project, ProjectRole
from app.db.session import get_db
from app.schemas.host_test_schemas import HostTestBatch, HostTestResult, HostTestUpdate, TestStatus
from app.services import agent_evidence_service
from app.services import host_test_service as tests
from app.services.agent_session_service import note_agent_model
from app.services.proposal_service import Attribution


@dataclass
class Actor:
    project_id: int
    who: Attribution


def human_reader(project: Project = Depends(get_current_project), user: User = Depends(get_current_user)):
    return Actor(project.id, Attribution(user_id=user.id))


def human_writer(project: Project = Depends(get_current_project),
                 user: User = Depends(require_project_role(ProjectRole.ANALYST))):
    return Actor(project.id, Attribution(user_id=user.id))


def agent_actor(request: Request, agent: Agent = Depends(check_agent_rate_limit), db: Session = Depends(get_db)):
    session = load_agent_session(db, request)
    return Actor(agent.project_id, Attribution(user_id=session.started_by_id, session=session))


def make_router(reader, writer):
    router = APIRouter()

    @router.post("/host-tests", status_code=201, summary="Propose tests directly on hosts")
    def create(body: HostTestBatch, actor: Actor = Depends(writer), db: Session = Depends(get_db)):
        if actor.who.session and body.agent_model:
            note_agent_model(actor.who.session, body.agent_model)
            actor.who.model = body.agent_model
        rows = tests.create_tests(db, actor.project_id, body.tests, actor.who)
        # Built before the commit: it expires every row, and reading each id
        # afterwards is one SELECT per test (202 for a full batch).
        items = tests.serialize_many(db, rows)
        db.commit()
        return {"items": items}

    @router.get("/host-tests", summary="Read proposed and completed host tests")
    def listing(host_id: int | None = Query(None, gt=0), status: TestStatus | None = None,
                label: str | None = Query(None, max_length=255), assigned_to_id: int | None = Query(None, gt=0),
                agent_session_id: int | None = Query(None, gt=0), mine: bool = False, q: str | None = None, active_only: bool = False,
                limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                actor: Actor = Depends(reader), db: Session = Depends(get_db)):
        require_project_host(db, actor.project_id, host_id)
        return tests.list_tests(db, actor.project_id, actor.who.user_id, host_id=host_id, status=status,
                                label=label, assigned_to_id=assigned_to_id, agent_session_id=agent_session_id,
                                mine=mine, q=q, active_only=active_only, limit=limit, offset=offset)

    @router.get("/host-tests/{test_id}", summary="Read one host test")
    def detail(test_id: int, actor: Actor = Depends(reader), db: Session = Depends(get_db)):
        return tests.serialize_many(db, [tests.get_test(db, actor.project_id, test_id)])[0]

    @router.patch("/host-tests/{test_id}", summary="Claim, dismiss or update a host test")
    def change(test_id: int, body: HostTestUpdate, actor: Actor = Depends(writer), db: Session = Depends(get_db)):
        row = tests.update_test(db, actor.project_id, test_id, body, actor.who.user_id)
        data = tests.serialize_many(db, [row])[0]
        db.commit()
        return data

    return router


router = make_router(human_reader, human_writer)


# A person's shortcut (v2.443.0): the evidence record and the status change in
# one call.  Page route only — an agent already has both calls
# (`POST /agent/evidence` with host_test_id, then PATCH), so this is a
# composition of the shared services, not a second contract.
@router.post("/host-tests/{test_id}/result", status_code=201, summary="Record the result of a test you ran")
def record_result(test_id: int, body: HostTestResult, actor: Actor = Depends(human_writer),
                  db: Session = Depends(get_db)):
    row, record = tests.record_result(db, actor.project_id, test_id, body, actor.who.user_id)
    data = {"test": tests.serialize_many(db, [row])[0], "evidence": agent_evidence_service.serialize_evidence(record)}
    db.commit()
    return data
agent_router = make_router(agent_actor, agent_actor)  # operator write gate is router-level in api.py
