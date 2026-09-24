import pytest
from conftest import Clock, at, chain_keys, emit_at, make_engine, only_finding, tick_at

from matter_health import kinds
from matter_health.engine import Context, Engine
from matter_health.model import Confidence, Finding, Severity
from matter_health.rules.pairing import PairingRule
from matter_health.store import Store


@pytest.fixture
def engine(ctx: Context) -> Engine:
    ctx.names.set("node:42", "Living Room Plug")
    return make_engine(ctx, rules=[PairingRule])


def rule(engine: Engine) -> PairingRule:
    found = engine.rules[0]
    assert isinstance(found, PairingRule)
    return found


def failure_chain(phase: str) -> list[str]:
    return [
        f"cause.pairing.{phase}",
        "link.pairing_stopped",
        "link.pairing_failed_impact",
        f"fix.pairing.{phase}",
        "fix.pairing_reset_and_retry",
    ]


async def by_key(store: Store, minutes: float) -> Finding:
    found = await store.finding(f"pairing:{at(minutes).isoformat()}")
    assert found is not None
    return found


async def test_a_successful_pairing(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await emit_at(ctx, clock, 0, kinds.COMMISSIONING_CONTACT)
    await emit_at(ctx, clock, 0.1, kinds.COMMISSIONING_CONTACT_OK)
    await emit_at(ctx, clock, 0.2, kinds.COMMISSIONING_STARTED, "node:42", node_id=42)
    await emit_at(ctx, clock, 0.3, kinds.COMMISSIONING_STEP, step="1", name="Reconnect")
    await emit_at(ctx, clock, 1, kinds.COMMISSIONING_COMPLETED, "node:42", node_id=42)

    finding = await only_finding(store)
    assert finding.key == f"pairing:{at(0).isoformat()}"
    assert finding.severity is Severity.INFO
    assert finding.title == "finding.pairing_ok.title"
    assert finding.params == {
        "device": "Living Room Plug",
        "phase": "finish",
        "step": "Reconnect",
        "node_id": 42,
        "reasons": [],
        "retries": 0,
    }
    assert finding.ended_at == at(1)
    assert finding.subjects == ["node:42"]
    assert chain_keys(finding) == ["link.pairing_ok"]
    assert finding.chain[0].params == {"device": "Living Room Plug", "seconds": 60}
    assert len(finding.chain[0].evidence) == 5
    assert rule(engine).attempt is None


async def test_a_failed_step_explains_its_stage(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await emit_at(ctx, clock, 0, kinds.COMMISSIONING_CONTACT)
    await emit_at(ctx, clock, 0.2, kinds.COMMISSIONING_STARTED, "node:43", node_id=43)
    await emit_at(
        ctx,
        clock,
        0.3,
        kinds.COMMISSIONING_STEP,
        step="6",
        name="OperationalCredentials.Certificates",
    )
    await emit_at(
        ctx,
        clock,
        0.4,
        kinds.COMMISSIONING_RETRY,
        step="6",
        name="OperationalCredentials.Certificates",
        reason="Timeout",
    )
    await emit_at(
        ctx,
        clock,
        0.5,
        kinds.COMMISSIONING_FAILED,
        step="6",
        name="OperationalCredentials.Certificates",
        reason="No response",
    )

    finding = await only_finding(store)
    assert finding.severity is Severity.PROBLEM
    assert finding.title == "finding.pairing_failed.title"
    assert finding.params["device"] is None
    assert finding.params["phase"] == "keys"
    assert finding.params["retries"] == 1
    assert finding.params["reasons"] == ["Timeout", "No response"]
    assert finding.subjects == ["node:43"]
    assert chain_keys(finding) == failure_chain("keys")
    assert finding.chain[0].confidence is Confidence.POSSIBLE

    # The overall result follows the step's failure and joins the attempt.
    await emit_at(ctx, clock, 0.6, kinds.COMMISSIONING_FAILED, reason="Timed out")
    finding = await only_finding(store)
    assert finding.params["reasons"] == ["Timeout", "No response", "Timed out"]
    assert finding.params["phase"] == "keys"

    await tick_at(engine, clock, 0.7)
    assert rule(engine).attempt is not None
    await tick_at(engine, clock, 1.2)
    assert rule(engine).attempt is None


async def test_a_rejected_certificate(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await emit_at(ctx, clock, 0, kinds.COMMISSIONING_CONTACT)
    await emit_at(ctx, clock, 0.1, kinds.COMMISSIONING_CONTACT_OK)
    await emit_at(
        ctx, clock, 0.2, kinds.COMMISSIONING_ATTESTATION_REJECTED, reason="untrusted"
    )
    await emit_at(ctx, clock, 0.3, kinds.COMMISSIONING_FAILED)

    finding = await only_finding(store)
    assert finding.params["phase"] == "authenticity"
    assert finding.params["reasons"] == ["untrusted"]
    assert finding.subjects == []
    assert chain_keys(finding) == failure_chain("authenticity")


async def test_an_attempt_that_goes_silent_is_given_up(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await tick_at(engine, clock, 0)
    await emit_at(ctx, clock, 0, kinds.COMMISSIONING_CONTACT)
    await emit_at(ctx, clock, 0.1, kinds.COMMISSIONING_CONTACT_OK)
    await tick_at(engine, clock, 2)
    assert await store.findings() == []

    await tick_at(engine, clock, 3.1)

    finding = await only_finding(store)
    assert finding.params["phase"] == "prepare"
    assert chain_keys(finding) == failure_chain("prepare")
    assert rule(engine).attempt is None


async def test_a_second_contact_within_an_attempt_continues_it(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await emit_at(ctx, clock, 0, kinds.COMMISSIONING_CONTACT)
    await emit_at(ctx, clock, 0.5, kinds.COMMISSIONING_CONTACT)
    await emit_at(ctx, clock, 1, kinds.COMMISSIONING_FAILED, reason="gave up")

    finding = await only_finding(store)
    assert finding.started_at == at(0)
    assert finding.params["phase"] == "first_contact"


async def test_a_new_contact_after_a_failure_is_a_new_attempt(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await emit_at(ctx, clock, 0, kinds.COMMISSIONING_CONTACT)
    await emit_at(ctx, clock, 0.2, kinds.COMMISSIONING_FAILED)
    await emit_at(ctx, clock, 0.4, kinds.COMMISSIONING_CONTACT)
    await emit_at(ctx, clock, 0.6, kinds.COMMISSIONING_COMPLETED, node_id=42)

    assert (await by_key(store, 0)).title == "finding.pairing_failed.title"
    assert (await by_key(store, 0.4)).title == "finding.pairing_ok.title"


async def test_a_long_pause_ends_the_previous_attempt(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await emit_at(ctx, clock, 0, kinds.COMMISSIONING_CONTACT)
    await emit_at(
        ctx, clock, 0.5, kinds.COMMISSIONING_STEP, step="11", name="ThreadNetworkSetup"
    )
    await emit_at(
        ctx, clock, 3, kinds.COMMISSIONING_STEP, step="1", name="GetInitialData"
    )

    first = await only_finding(store)
    assert first.key == f"pairing:{at(0).isoformat()}"
    assert first.params["phase"] == "network"
    attempt = rule(engine).attempt
    assert attempt is not None
    assert attempt.start == at(3)


async def test_a_disturbed_mesh_is_the_likely_cause(
    ctx: Context, store: Store, clock: Clock, engine: Engine
) -> None:
    await emit_at(ctx, clock, 0, kinds.THREAD_FOREIGN_PARTITION)
    await emit_at(ctx, clock, 1, kinds.COMMISSIONING_CONTACT)
    await emit_at(
        ctx, clock, 1.5, kinds.COMMISSIONING_FAILED, name="Reconnect", reason="x"
    )

    finding = await only_finding(store)
    assert chain_keys(finding) == [
        "link.mesh_disturbed_during_pairing",
        "link.pairing_stopped",
        "link.pairing_failed_impact",
        "fix.pairing_wait_for_mesh",
        "fix.pairing.find_again",
        "fix.pairing_reset_and_retry",
    ]
    assert finding.chain[0].confidence is Confidence.LIKELY
    assert finding.chain[0].at == at(0)
