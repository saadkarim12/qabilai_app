"""Integration tests for the job background pipeline (Step 2.6).

Strategy
--------

* The *async helpers* (``embed_jd``, ``generate_questions_for_job``) are
  tested directly with the per-test transactional ``db_session`` and
  mocked AI calls. This is the only path that can run inside the test's
  outer transaction — Celery's eager mode would otherwise create its own
  session and leak rows.
* The *route triggering* path is tested by patching the task wrappers'
  ``apply_async`` methods and asserting they're called with the right
  ``job_id`` after PATCH /jobs/{id}/status.
* The *retry config* is asserted as task-attribute introspection — a
  real broker would require Docker plumbing we don't ship in CI.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.job import Job
from src.db.models.user import User
from src.enums.jobs import (
    EmploymentType,
    JobStatus,
    NoticePeriod,
    VisaRequirement,
    WorkMode,
)
from src.enums.pipeline import PipelineStep, PipelineStepState
from src.enums.roles import UserRole
from src.schemas.whatsapp_question import WhatsAppQuestion
from src.utils.password import hash_password
from src.utils.slug import generate_public_slug
from src.workers.tasks import job_pipeline
from src.workers.tasks.job_pipeline import (
    PIPELINE_MAX_RETRIES,
    TransientPipelineError,
    _merge_step_state,
    embed_jd,
    embed_jd_task,
    generate_questions_for_job,
    generate_questions_task,
)

pytestmark = pytest.mark.integration


# --- Helpers ----------------------------------------------------------------


async def _seed_user(
    db_session: AsyncSession, password: str = "hunter2-correct"
) -> tuple[User, str]:
    user = User(
        email=f"hr-{uuid.uuid4().hex[:8]}@kabil.dev",
        password_hash=hash_password(password),
        full_name="HR Person",
        role=UserRole.ADMIN,
    )
    db_session.add(user)
    await db_session.flush()
    return user, password


async def _seed_job(db_session: AsyncSession, user: User) -> Job:
    job = Job(
        created_by=user.id,
        title="Senior Backend Engineer",
        hiring_company="Kabil Test Co",
        country="AE",
        city="Dubai",
        employment_type=EmploymentType.PERMANENT,
        work_mode=WorkMode.HYBRID,
        currency="AED",
        min_salary=20_000,
        max_salary=30_000,
        notice_period=NoticePeriod.DAYS_30,
        min_experience_years=5,
        required_skills=["Python", "PostgreSQL"],
        preferred_skills=["AWS"],
        visa_requirement=VisaRequirement.SPONSORSHIP_OFFERED,
        nationality_preference=[],
        languages_required=["english", "arabic"],
        ai_verify_flags={},
        job_description="Build the Kabil hiring backend. Strong async Python.",
        status=JobStatus.DRAFT,
        public_slug=generate_public_slug(),
        whatsapp_questions=[],
        pipeline_status={},
        ready_for_applications=False,
    )
    db_session.add(job)
    await db_session.flush()
    return job


def _fake_question(idx: int) -> WhatsAppQuestion:
    return WhatsAppQuestion.model_validate(
        {
            "id": f"q_fake{idx:04d}",
            "order": idx,
            "category": "background_validation",
            "subcategory": "Experience",
            "question_en": f"Question {idx} EN?",
            "question_ar": f"سؤال {idx} بالعربية؟",
            "reasoning": "Validates the candidate claim.",
            "is_ai_generated": True,
            "source_field": None,
        }
    )


async def _login(client: httpx.AsyncClient, email: str, password: str) -> str:
    resp = await client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return str(resp.json()["access_token"])


# --- Async helper tests ------------------------------------------------------


async def test_embed_jd_writes_vector_and_marks_ok(
    migrations_applied: None,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, _ = await _seed_user(db_session)
    job = await _seed_job(db_session, user)

    async def _fake_embed(text: str) -> list[float]:
        assert "Kabil" in text
        return [0.1] * 1536

    monkeypatch.setattr(job_pipeline, "embed_text", _fake_embed)

    await embed_jd(job.id, session=db_session)
    await db_session.refresh(job)

    assert job.jd_embedding is not None
    assert len(job.jd_embedding) == 1536
    assert job.pipeline_status[PipelineStep.EMBEDDING.value] == PipelineStepState.OK.value
    # questions step hasn't run, so ready stays False
    assert job.ready_for_applications is False


async def test_generate_questions_writes_jsonb_and_marks_ok(
    migrations_applied: None,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, _ = await _seed_user(db_session)
    job = await _seed_job(db_session, user)

    async def _fake_generate(*, job: Job) -> list[WhatsAppQuestion]:
        return [_fake_question(1), _fake_question(2)]

    monkeypatch.setattr(job_pipeline, "generate_whatsapp_questions", _fake_generate)

    await generate_questions_for_job(job.id, session=db_session)
    await db_session.refresh(job)

    assert len(job.whatsapp_questions) == 2
    assert job.pipeline_status[PipelineStep.QUESTIONS.value] == PipelineStepState.OK.value
    assert job.ready_for_applications is False  # embedding step still missing


async def test_both_steps_flip_ready_for_applications(
    migrations_applied: None,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, _ = await _seed_user(db_session)
    job = await _seed_job(db_session, user)

    async def _fake_embed(text: str) -> list[float]:
        return [0.5] * 1536

    async def _fake_generate(*, job: Job) -> list[WhatsAppQuestion]:
        return [_fake_question(1)]

    monkeypatch.setattr(job_pipeline, "embed_text", _fake_embed)
    monkeypatch.setattr(job_pipeline, "generate_whatsapp_questions", _fake_generate)

    await embed_jd(job.id, session=db_session)
    await generate_questions_for_job(job.id, session=db_session)
    await db_session.refresh(job)

    assert job.ready_for_applications is True
    assert job.jd_embedding is not None
    assert len(job.whatsapp_questions) == 1


async def test_embed_jd_idempotent_skips_when_already_populated(
    migrations_applied: None,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, _ = await _seed_user(db_session)
    job = await _seed_job(db_session, user)
    job.jd_embedding = [0.9] * 1536
    await db_session.flush()

    calls: list[str] = []

    async def _fake_embed(text: str) -> list[float]:
        calls.append(text)
        return [0.0] * 1536

    monkeypatch.setattr(job_pipeline, "embed_text", _fake_embed)
    await embed_jd(job.id, session=db_session)

    # Helper bailed out without calling the embedder again.
    assert calls == []


async def test_generate_questions_idempotent_skips_when_already_populated(
    migrations_applied: None,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, _ = await _seed_user(db_session)
    job = await _seed_job(db_session, user)
    job.whatsapp_questions = [_fake_question(1).model_dump(mode="json")]
    await db_session.flush()

    calls: list[Job] = []

    async def _fake_generate(*, job: Job) -> list[WhatsAppQuestion]:
        calls.append(job)
        return []

    monkeypatch.setattr(job_pipeline, "generate_whatsapp_questions", _fake_generate)
    await generate_questions_for_job(job.id, session=db_session)
    assert calls == []


async def test_embed_jd_non_transient_failure_marks_failed(
    migrations_applied: None,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, _ = await _seed_user(db_session)
    job = await _seed_job(db_session, user)

    async def _boom(text: str) -> list[float]:
        raise RuntimeError("permanent boom")

    monkeypatch.setattr(job_pipeline, "embed_text", _boom)
    await embed_jd(job.id, session=db_session)
    await db_session.refresh(job)

    assert job.pipeline_status[PipelineStep.EMBEDDING.value] == PipelineStepState.FAILED.value
    assert "permanent boom" in job.pipeline_status["embedding_error"]
    assert job.ready_for_applications is False


async def test_embed_jd_transient_error_raises_for_retry(
    migrations_applied: None,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, _ = await _seed_user(db_session)
    job = await _seed_job(db_session, user)

    # openai.APIConnectionError is in the transient tuple; instantiate it
    # via its public ctor with a synthetic request.
    import httpx as _httpx
    import openai as _openai

    class _Dummy(_openai.APIConnectionError):
        def __init__(self) -> None:
            super().__init__(request=_httpx.Request("POST", "http://test"))

    async def _flaky(text: str) -> list[float]:
        raise _Dummy()

    monkeypatch.setattr(job_pipeline, "embed_text", _flaky)
    with pytest.raises(TransientPipelineError):
        await embed_jd(job.id, session=db_session)

    await db_session.refresh(job)
    assert job.pipeline_status[PipelineStep.EMBEDDING.value] == PipelineStepState.PENDING.value


async def test_generate_questions_non_transient_failure_marks_failed(
    migrations_applied: None,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, _ = await _seed_user(db_session)
    job = await _seed_job(db_session, user)

    async def _boom(*, job: Job) -> list[WhatsAppQuestion]:
        raise RuntimeError("claude said no")

    monkeypatch.setattr(job_pipeline, "generate_whatsapp_questions", _boom)
    await generate_questions_for_job(job.id, session=db_session)
    await db_session.refresh(job)

    assert job.pipeline_status[PipelineStep.QUESTIONS.value] == PipelineStepState.FAILED.value
    assert "claude said no" in job.pipeline_status["questions_error"]


async def test_missing_job_logs_and_returns(
    migrations_applied: None,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bogus = uuid.uuid4()

    # No error raised; helper just logs and returns. We're verifying the
    # graceful path exists so a stale task doesn't crash a worker.
    await embed_jd(bogus, session=db_session)
    await generate_questions_for_job(bogus, session=db_session)


# --- pipeline_status atomic-merge (post-fix race guard) ---------------------


async def test_merge_step_state_preserves_existing_keys(
    migrations_applied: None,
    db_session: AsyncSession,
) -> None:
    """Atomic-merge guard.

    Before the Step 2.6 fix, ``_set_step_state`` did a Python-side
    read-modify-write on ``job.pipeline_status``. Two parallel tasks
    each loaded the row before either committed, so each emitted a
    full-replace UPDATE -- the second to commit silently dropped the
    first's key. The atomic SQL merge (``pipeline_status - error_key
    || patch::jsonb``) preserves every prior key because Postgres holds
    a row lock for the duration of the UPDATE.

    Reproduction shape: seed a pre-existing key in pipeline_status, then
    invoke the merge for a *different* step. Both keys must survive.
    """
    user, _ = await _seed_user(db_session)
    job = await _seed_job(db_session, user)

    # Simulate "the other task already finished" by seeding the
    # questions key directly via SQL (bypasses the helper under test).
    await db_session.execute(
        sa.text("UPDATE jobs SET pipeline_status = CAST(:ps AS JSONB) WHERE id = :id"),
        {"ps": '{"questions": "ok"}', "id": str(job.id)},
    )
    await db_session.flush()

    # Now merge the embedding key; this is the call that would have
    # clobbered ``questions`` under the old implementation.
    new_status = await _merge_step_state(
        db_session,
        job.id,
        PipelineStep.EMBEDDING,
        PipelineStepState.OK,
    )

    assert new_status is not None
    assert new_status[PipelineStep.EMBEDDING.value] == PipelineStepState.OK.value
    assert new_status[PipelineStep.QUESTIONS.value] == PipelineStepState.OK.value


async def test_merge_step_state_clears_prior_error_key_on_recovery(
    migrations_applied: None,
    db_session: AsyncSession,
) -> None:
    """A successful retry must remove the old ``{step}_error`` key.

    Same shape as the pre-fix behavior, just via SQL: ``- error_key``
    runs before ``|| patch`` so the error message from a previous failed
    attempt is gone once the step transitions to ``ok``.
    """
    user, _ = await _seed_user(db_session)
    job = await _seed_job(db_session, user)

    await db_session.execute(
        sa.text("UPDATE jobs SET pipeline_status = CAST(:ps AS JSONB) WHERE id = :id"),
        {
            "ps": '{"embedding": "failed", "embedding_error": "boom"}',
            "id": str(job.id),
        },
    )
    await db_session.flush()

    new_status = await _merge_step_state(
        db_session,
        job.id,
        PipelineStep.EMBEDDING,
        PipelineStepState.OK,
    )

    assert new_status is not None
    assert new_status[PipelineStep.EMBEDDING.value] == PipelineStepState.OK.value
    assert "embedding_error" not in new_status


# --- Route triggering --------------------------------------------------------


class _ApplyAsyncSpy:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append((args, kwargs))


async def test_open_route_enqueues_both_tasks(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, password = await _seed_user(db_session)
    job = await _seed_job(db_session, user)
    token = await _login(client, user.email, password)

    embed_spy = _ApplyAsyncSpy()
    gen_spy = _ApplyAsyncSpy()
    monkeypatch.setattr(embed_jd_task, "apply_async", embed_spy)
    monkeypatch.setattr(generate_questions_task, "apply_async", gen_spy)

    resp = await client.patch(
        f"/jobs/{job.id}/status",
        headers={"Authorization": f"Bearer {token}"},
        json={"status": "open"},
    )
    assert resp.status_code == 200

    assert len(embed_spy.calls) == 1
    assert embed_spy.calls[0][1].get("args") == [str(job.id)]
    assert len(gen_spy.calls) == 1
    assert gen_spy.calls[0][1].get("args") == [str(job.id)]


async def test_close_then_reopen_after_ready_does_not_re_enqueue(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, password = await _seed_user(db_session)
    job = await _seed_job(db_session, user)
    job.status = JobStatus.CLOSED  # pretend it was already opened + closed
    job.ready_for_applications = True
    await db_session.flush()
    await db_session.execute(sa.text("SELECT 1"))  # avoid pending state surprises

    token = await _login(client, user.email, password)
    embed_spy = _ApplyAsyncSpy()
    gen_spy = _ApplyAsyncSpy()
    monkeypatch.setattr(embed_jd_task, "apply_async", embed_spy)
    monkeypatch.setattr(generate_questions_task, "apply_async", gen_spy)

    resp = await client.patch(
        f"/jobs/{job.id}/status",
        headers={"Authorization": f"Bearer {token}"},
        json={"status": "open"},
    )
    assert resp.status_code == 200

    # ready_for_applications already True → no re-dispatch.
    assert embed_spy.calls == []
    assert gen_spy.calls == []


async def test_close_route_does_not_enqueue(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, password = await _seed_user(db_session)
    job = await _seed_job(db_session, user)
    job.status = JobStatus.OPEN
    await db_session.flush()
    token = await _login(client, user.email, password)

    embed_spy = _ApplyAsyncSpy()
    gen_spy = _ApplyAsyncSpy()
    monkeypatch.setattr(embed_jd_task, "apply_async", embed_spy)
    monkeypatch.setattr(generate_questions_task, "apply_async", gen_spy)

    resp = await client.patch(
        f"/jobs/{job.id}/status",
        headers={"Authorization": f"Bearer {token}"},
        json={"status": "closed"},
    )
    assert resp.status_code == 200
    assert embed_spy.calls == []
    assert gen_spy.calls == []


# --- Schema visibility -------------------------------------------------------


async def test_detail_response_includes_pipeline_fields(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Stub the route's redis cache so the test isn't broker-dependent.
    from src.services import job_service

    class _FakeRedis:
        def __init__(self) -> None:
            self.store: dict[str, bytes] = {}

        async def get(self, key: str) -> bytes | None:
            return self.store.get(key)

        async def set(self, key: str, value: str | bytes, ex: int | None = None) -> None:
            self.store[key] = value.encode() if isinstance(value, str) else value

    monkeypatch.setattr(job_service, "_redis_client", lambda: _FakeRedis())

    user, password = await _seed_user(db_session)
    job = await _seed_job(db_session, user)
    job.pipeline_status = {"embedding": "ok"}
    await db_session.flush()
    token = await _login(client, user.email, password)

    resp = await client.get(f"/jobs/{job.id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["pipeline_status"] == {"embedding": "ok"}
    assert body["ready_for_applications"] is False


async def test_list_response_includes_pipeline_fields(
    migrations_applied: None,
    db_session: AsyncSession,
    client: httpx.AsyncClient,
) -> None:
    user, password = await _seed_user(db_session)
    job = await _seed_job(db_session, user)
    job.pipeline_status = {"questions": "failed", "questions_error": "boom"}
    await db_session.flush()
    token = await _login(client, user.email, password)

    resp = await client.get("/jobs", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    items = resp.json()["items"]
    row = next(i for i in items if i["id"] == str(job.id))
    assert row["pipeline_status"] == {"questions": "failed", "questions_error": "boom"}
    assert row["ready_for_applications"] is False


# --- Retry config sanity -----------------------------------------------------


def test_embed_task_retry_config_is_correct() -> None:
    assert TransientPipelineError in embed_jd_task.autoretry_for
    assert embed_jd_task.retry_kwargs["max_retries"] == PIPELINE_MAX_RETRIES


def test_generate_questions_task_retry_config_is_correct() -> None:
    assert TransientPipelineError in generate_questions_task.autoretry_for
    assert generate_questions_task.retry_kwargs["max_retries"] == PIPELINE_MAX_RETRIES
