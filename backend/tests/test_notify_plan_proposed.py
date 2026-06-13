"""NotificationService.notify_plan_proposed nudges plan approvers.

Plan submission (agent → human review) is the one human gate in the agent
loop and previously had no notification. Approvers are project members with
admin/analyst role; viewers don't approve, so they're not pinged.
"""
from datetime import datetime, timezone

from app.db.models_auth import User, UserRole
from app.db.models_project import Notification, ProjectMembership, ProjectRole
from app.db.models_agent import TestPlan
from app.services.notification_service import NotificationService


def _user(db, username, uid):
    u = User(
        id=uid, username=username, email=f"{username}@example.com",
        full_name=username, hashed_password="x", role=UserRole.MEMBER,
        is_active=True, is_verified=True, created_at=datetime.now(timezone.utc),
    )
    db.add(u)
    db.flush()
    return u


def _member(db, project_id, user_id, role):
    db.add(ProjectMembership(project_id=project_id, user_id=user_id, role=role))


def test_notifies_admin_and_analyst_not_viewer(db_session, test_project, test_agent):
    pid = test_project.id
    admin = _user(db_session, "approver-admin", 101)
    analyst = _user(db_session, "approver-analyst", 102)
    viewer = _user(db_session, "watcher-viewer", 103)
    _member(db_session, pid, admin.id, ProjectRole.ADMIN.value)
    _member(db_session, pid, analyst.id, ProjectRole.ANALYST.value)
    _member(db_session, pid, viewer.id, ProjectRole.VIEWER.value)

    plan = TestPlan(
        project_id=pid, agent_id=test_agent.id, title="Q2 external",
        status="proposed",
    )
    db_session.add(plan)
    db_session.flush()

    created = NotificationService(db_session).notify_plan_proposed(plan, pid)
    db_session.commit()

    notified = {n.user_id for n in created}
    assert notified == {admin.id, analyst.id}  # viewer excluded

    rows = db_session.query(Notification).filter(
        Notification.source_type == "test_plan",
        Notification.source_id == plan.id,
    ).all()
    assert len(rows) == 2
    assert all(n.type == "plan_proposed" for n in rows)
    assert all("Q2 external" in n.title for n in rows)
    assert all(n.actor_id is None for n in rows)  # agent actor, no user


def test_no_approvers_yields_no_notifications(db_session, test_project, test_agent):
    pid = test_project.id
    viewer = _user(db_session, "only-viewer", 110)
    _member(db_session, pid, viewer.id, ProjectRole.VIEWER.value)
    plan = TestPlan(project_id=pid, agent_id=test_agent.id, title="t", status="proposed")
    db_session.add(plan)
    db_session.flush()

    created = NotificationService(db_session).notify_plan_proposed(plan, pid)
    assert created == []


def test_notify_plan_ready_to_close(db_session, test_project, test_agent):
    pid = test_project.id
    analyst = _user(db_session, "closer-analyst", 120)
    viewer = _user(db_session, "closer-viewer", 121)
    _member(db_session, pid, analyst.id, ProjectRole.ANALYST.value)
    _member(db_session, pid, viewer.id, ProjectRole.VIEWER.value)
    plan = TestPlan(project_id=pid, agent_id=test_agent.id, title="DC sweep", status="in_progress")
    db_session.add(plan)
    db_session.flush()

    created = NotificationService(db_session).notify_plan_ready_to_close(plan, pid)
    db_session.commit()

    assert {n.user_id for n in created} == {analyst.id}  # viewer excluded
    rows = db_session.query(Notification).filter(
        Notification.type == "plan_ready", Notification.source_id == plan.id,
    ).all()
    assert len(rows) == 1
    assert "DC sweep" in rows[0].title and "ready to close" in rows[0].title
