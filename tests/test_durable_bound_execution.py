"""Controlled adapters with a real temporary journal and mocked external effects."""

from unittest.mock import Mock

import pytest

from agent import procedure_execution as execution
from agent.tools import bound_executor as module
from agent.tools import recovery_verifier as recovery
from core.operation_journal import AttemptLease, JournalDenied
from tests.test_bound_execution import factory as factory, issue, assert_no_effect
from tests.test_bound_recovery import prepared as prepared, verify


def test_durable_preparing_state_is_visible_before_docker_is_opened(factory, monkeypatch):
    case = factory()
    grant = issue(case)
    store = execution._operation_journal()

    def open_client():
        row = store.inspect()[0]
        assert row["stage"] == "preparing"
        assert row["attempt_id"] == grant.snapshot()["durable_attempt_id"]
        return case.client

    monkeypatch.setattr(module, "_docker_client", Mock(side_effect=open_client))
    result = module.execute_bound_procedure(grant, case.graph)
    assert result["operation_confirmed"] and result["journal_persistence"] == "confirmed"
    assert store.inspect()[0]["stage"] == "operation_confirmed"


def test_fixed_effect_runs_only_after_committed_admission(factory):
    case = factory()
    store = execution._operation_journal()

    def mutation(**kwargs):
        row = store.inspect(receipt=True)[0]
        assert row["stage"] == row["receipt"]["state"] == "effect_attempted"
        assert row["receipt"]["action_dispatched"]

    case.container.restart.side_effect = mutation
    assert module.execute_bound_procedure(issue(case), case.graph)["operation_confirmed"]
    case.container.restart.assert_called_once()


def test_new_controller_cannot_bypass_a_previous_attempt(factory):
    first = factory()
    assert module.execute_bound_procedure(issue(first), first.graph)["operation_confirmed"]
    second = factory(binding=("paymentservice", "CONNECTION_REFUSED"))
    result = module.execute_bound_procedure(issue(second), second.graph)
    assert result["request_outcome"] == "rejected" and not result["request_action_dispatched"]
    assert result["reason"] == "durable_attempt_reservation_denied"
    assert result["journal_denial"] == "deployment_review_required"
    module._docker_client.assert_not_called()
    assert_no_effect(second)


def test_journal_unavailable_fails_before_docker_and_hides_exception_text(factory, monkeypatch):
    case = factory()
    monkeypatch.setattr(execution, "_operation_journal", Mock(side_effect=OSError("PRIVATE-PATH")))
    result = module.execute_bound_procedure(issue(case), case.graph)
    assert result["request_outcome"] == "rejected" and "PRIVATE" not in str(result)
    module._docker_client.assert_not_called()
    assert_no_effect(case)


def test_reservation_ack_loss_blocks_all_new_dispatch_even_without_a_handle(factory, monkeypatch):
    case = factory()
    store = execution._operation_journal()
    reserve = store.reserve

    def lost(*args):
        lease = reserve(*args)
        lease.close()
        raise OSError("PRIVATE-LOST-ACK")

    monkeypatch.setattr(store, "reserve", lost)
    monkeypatch.setattr(execution, "_operation_journal", lambda: store)
    assert module.execute_bound_procedure(issue(case), case.graph)["request_outcome"] == "rejected"
    assert store.inspect()[0]["stage"] == "preparing"
    module._docker_client.assert_not_called()


@pytest.mark.parametrize("phase", ["preparing", "effect_attempted"])
def test_unconfirmed_admission_or_preparation_write_never_calls_fixed_effect(factory, monkeypatch, phase):
    case = factory()
    original = AttemptLease.record

    def fail(self, stage, receipt):
        if stage == phase:
            raise OSError("PRIVATE-DISK")
        return original(self, stage, receipt)

    monkeypatch.setattr(AttemptLease, "record", fail)
    result = module.execute_bound_procedure(issue(case), case.graph)
    assert not result["request_action_dispatched"] and not result["operation_confirmed"]
    assert result["journal_persistence"] == "unconfirmed"
    assert "PRIVATE" not in str(result)
    assert_no_effect(case)


@pytest.mark.parametrize("committed", [False, True])
def test_effect_acknowledgement_failure_is_uncertain_and_cannot_retry(factory, monkeypatch, committed):
    case = factory()
    original = AttemptLease.record

    def fail(self, stage, receipt):
        if stage == "operation_confirmed":
            if committed:
                original(self, stage, receipt)
            raise OSError("PRIVATE-ACK-LOSS")
        return original(self, stage, receipt)

    monkeypatch.setattr(AttemptLease, "record", fail)
    grant = issue(case)
    result = module.execute_bound_procedure(grant, case.graph)
    assert result["state"] == "effect_uncertain" and result["action_dispatched"]
    assert not result["operation_confirmed"] and result["journal_persistence"] == "unconfirmed"
    assert execution._operation_journal().inspect()[0]["stage"] == ("operation_confirmed" if committed else "effect_attempted")
    assert module.execute_bound_procedure(grant, case.graph)["request_outcome"] == "rejected"
    case.container.restart.assert_called_once()


def test_journal_latency_does_not_extend_context_freshness(factory, monkeypatch):
    case = factory()
    original = AttemptLease.record

    def slow(self, stage, receipt):
        result = original(self, stage, receipt)
        if stage == "effect_attempted":
            case.clock.value += 6
        return result

    monkeypatch.setattr(AttemptLease, "record", slow)
    result = module.execute_bound_procedure(issue(case), case.graph)
    assert result["state"] == "effect_uncertain"
    assert execution._operation_journal().inspect()[0]["stage"] == "effect_uncertain"
    assert_no_effect(case)


def test_journal_expiry_after_commit_denies_sdk_dispatch(factory, monkeypatch):
    case = factory()
    original = AttemptLease.record

    def slow(self, stage, receipt):
        result = original(self, stage, receipt)
        if stage == "effect_attempted":
            case.clock.value += 31
        return result

    monkeypatch.setattr(AttemptLease, "record", slow)
    assert module.execute_bound_procedure(issue(case), case.graph)["state"] == "effect_uncertain"
    assert_no_effect(case)


def test_durable_verification_preserves_full_receipt_and_requires_review(prepared):
    case = prepared()
    result = verify(case)
    assert result["outcome"] == "verified_recovery_window"
    store = execution._operation_journal()
    row = store.inspect(receipt=True)[0]
    assert row["stage"] == "verified_recovery_window" and not row["closed"]
    assert row["receipt"]["recovery"] == result
    assert row["receipt"]["recovery_verified"]
    assert store.acknowledge_terminal(row["attempt_id"], expected_version=row["version"], approve_review=True)["closed"]


def test_failed_checkout_is_durable_and_cannot_be_closed_as_recovery(prepared):
    from tests.test_bound_recovery import application

    case = prepared()
    case.canary.side_effect = lambda _: application(failed="checkout")
    assert verify(case)["outcome"] == "not_recovered"
    store = execution._operation_journal()
    row = store.inspect(receipt=True)[0]
    assert row["stage"] == "not_recovered" and row["receipt"]["recovery"]["transactions"][0]["outcome"] == "failed"
    with pytest.raises(JournalDenied):
        store.acknowledge_terminal(row["attempt_id"], expected_version=row["version"], approve_review=True)


def test_verification_start_requires_durable_ownership_before_any_probe(prepared, monkeypatch):
    case = prepared()
    monkeypatch.setattr(case.grant._lease.journal, "resume_verification", Mock(side_effect=OSError("PRIVATE")))
    result = verify(case)
    assert result["outcome"] == "rejected" and result["reason"] == "durable_verification_start_unconfirmed"
    recovery._docker_client.assert_not_called()
    case.canary.assert_not_called()
    assert not case.grant.snapshot()["recovery_verified"]


@pytest.mark.parametrize("committed", [False, True])
def test_recovery_receipt_write_uncertainty_cannot_return_verified(prepared, monkeypatch, committed):
    case = prepared()
    original = AttemptLease.record

    def fail(self, stage, receipt):
        if stage == "verified_recovery_window":
            if committed:
                original(self, stage, receipt)
            raise OSError("PRIVATE-VERIFICATION-ACK")
        return original(self, stage, receipt)

    monkeypatch.setattr(AttemptLease, "record", fail)
    result = verify(case)
    assert result["outcome"] == "inconclusive" and result["reason"] == "durable_verification_finish_unconfirmed"
    assert not case.grant.snapshot()["recovery_verified"]
    assert case.grant.snapshot()["operation_confirmed"]
    assert "PRIVATE" not in str(case.grant.snapshot())
    assert execution._operation_journal().inspect()[0]["stage"] == ("verified_recovery_window" if committed else "verifying")
    assert verify(case)["outcome"] == "rejected"
    assert case.canary.call_count == 4


def test_interrupted_verification_is_durable_and_consumes_attempt(prepared):
    case = prepared()
    case.canary.side_effect = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        verify(case)
    row = execution._operation_journal().inspect(receipt=True)[0]
    assert row["stage"] == "verification_inconclusive"
    assert row["receipt"]["recovery"]["reason"] == "recovery_verification_interrupted"
    assert verify(case)["outcome"] == "rejected"


def test_verification_timer_includes_journal_admission_latency(prepared, monkeypatch):
    case = prepared()
    original = AttemptLease.record

    def slow(self, stage, receipt):
        result = original(self, stage, receipt)
        if stage == "verifying":
            case.clock.value += 301
        return result

    monkeypatch.setattr(AttemptLease, "record", slow)
    result = verify(case)
    assert result["reason"] == "recovery_deadline_exhausted" and result["outcome"] == "inconclusive"
    recovery._docker_client.assert_not_called()
    case.canary.assert_not_called()


def test_uncertain_controller_exception_remains_durable(factory):
    case = factory()
    case.container.restart.side_effect = TimeoutError("PRIVATE")
    result = module.execute_bound_procedure(issue(case), case.graph)
    row = execution._operation_journal().inspect(receipt=True)[0]
    assert result["state"] == row["stage"] == "effect_uncertain"
    assert not row["receipt"]["operation_confirmed"]
    assert "PRIVATE" not in str(row)
