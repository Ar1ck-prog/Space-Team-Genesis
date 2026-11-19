from __future__ import annotations

import unittest

from space_team_genesis.environment import AuditEnvironment, EnvironmentRuntime, EpisodeData, EpisodeFinalizedError, EvaluationResult


def episode_data() -> EpisodeData:
    return EpisodeData(
        episode_id="ep_test_001",
        manifest={
            "schema_version": "1.0.0",
            "episode_id": "ep_test_001",
            "base_currency": "KZT",
            "currency_decimals": {"KZT": 2},
            "period_start": "2026-11-01T00:00:00+05:00",
            "period_end": "2026-11-30T23:59:59+05:00",
            "timezone": "Asia/Almaty",
            "record_counts": {"ledger": 2, "invoices": 1, "payments": 1, "fx": 1},
            "document_count": 1,
            "rounding_rule": "half_up_to_currency_minor",
        },
        policy_summary={
            "fx_rate_date": "payment_timestamp_local_date",
            "period_boundary": "inclusive_start_and_end",
            "rounding": "half_up_to_currency_minor",
        },
        records={
            "ledger": [
                {"id": "ledger:L-1", "timestamp": "2026-11-01T09:00:00+05:00", "account": "4010", "debit_minor": 100, "credit_minor": 0, "currency": "KZT", "entity_id": "entity:E-1", "invoice_id": "invoice:I-1", "payment_id": None},
                {"id": "ledger:L-2", "timestamp": "2026-11-02T09:00:00+05:00", "account": "4010", "debit_minor": 200, "credit_minor": 0, "currency": "KZT", "entity_id": "entity:E-1", "invoice_id": None, "payment_id": "payment:P-1"},
            ],
            "invoices": [{"id": "invoice:I-1", "currency": "KZT", "total_minor": 100}],
            "payments": [{"id": "payment:P-1", "currency": "KZT", "amount_minor": 200}],
            "fx": [{"id": "fx:2026-11-01:USD:KZT", "rate": "512.0000"}],
        },
        documents={"doc:D-1": {"id": "doc:D-1", "invoice_id": "invoice:I-1", "currency": "KZT", "total_minor": 100}},
    )


class FixedEvaluator:
    def evaluate(self, findings, inspection_steps):
        self.findings = findings
        self.inspection_steps = inspection_steps
        return EvaluationResult(reward=0.75)


class Generator:
    def generate(self, seed, config):
        self.seed = seed
        self.config = config
        return episode_data()


class AdvancingClock:
    def __init__(self):
        self.times = iter([0, 0, 61])

    def __call__(self):
        return next(self.times)


class EnvironmentTests(unittest.TestCase):
    def environment(self, *, mode="open"):
        return AuditEnvironment(episode_data(), evaluation_mode=mode, evaluator=FixedEvaluator())

    def test_reset_and_stable_pagination(self):
        environment = self.environment()
        reset = environment.reset()
        self.assertEqual(reset["observation"]["kind"], "episode_start")
        first = environment.step({"kind": "list_records", "table": "ledger", "cursor": None, "limit": 1})
        cursor = first["observation"]["next_cursor"]
        repeated = environment.step({"kind": "list_records", "table": "ledger", "cursor": None, "limit": 1})
        second = environment.step({"kind": "list_records", "table": "ledger", "cursor": cursor, "limit": 1})
        self.assertEqual(first["observation"]["records"], repeated["observation"]["records"])
        self.assertEqual(second["observation"]["records"][0]["id"], "ledger:L-2")
        self.assertEqual(second["observation"]["remaining_steps"], 97)

    def test_invalid_action_consumes_a_step_without_leaking_data(self):
        environment = self.environment()
        environment.reset()
        result = environment.step({"kind": "get_record", "table": "ledger", "id": "invoice:I-1"})
        self.assertEqual(result["observation"]["error"]["code"], "invalid_argument")
        self.assertEqual(result["info"]["step"], 1)
        self.assertNotIn("truth", str(result))

    def test_calculator_accepts_only_decimal_arithmetic(self):
        environment = self.environment()
        environment.reset()
        result = environment.step({"kind": "calculator", "expression": "(1.5 + 2) / 2"})
        self.assertEqual(result["observation"]["result"], "1.75")
        unsafe = environment.step({"kind": "calculator", "expression": "__import__('os').system('id')"})
        self.assertEqual(unsafe["observation"]["error"]["code"], "unsafe_expression")

    def test_submit_is_terminal_and_closed_mode_hides_reward(self):
        environment = self.environment(mode="closed")
        environment.reset()
        result = environment.step({"kind": "submit", "findings": {"episode_id": "ep_test_001", "findings": []}})
        self.assertTrue(result["terminated"])
        self.assertEqual(result["reward"], 0)
        with self.assertRaises(EpisodeFinalizedError):
            environment.step({"kind": "list_records", "table": "ledger", "cursor": None, "limit": 1})

    def test_invalid_submit_is_terminal_with_open_negative_reward(self):
        environment = self.environment()
        environment.reset()
        result = environment.step({"kind": "submit", "findings": {"episode_id": "other", "findings": []}})
        self.assertTrue(result["terminated"])
        self.assertEqual(result["reward"], -1)
        self.assertEqual(result["observation"]["error"]["code"], "invalid_submission")

    def test_submit_with_unhashable_ids_is_a_terminal_schema_error(self):
        environment = self.environment()
        environment.reset()
        result = environment.step({
            "kind": "submit", "findings": {"episode_id": "ep_test_001", "findings": [{
                "type": "fx_date_or_rate", "affected_ids": [{"id": "ledger:L-1"}],
                "claimed_amount_minor": 1, "currency": "KZT",
                "evidence_ids": ["ledger:L-1"], "explanation": "Invalid ID type",
            }]},
        })
        self.assertTrue(result["terminated"])
        self.assertEqual(result["reward"], -1)
        self.assertEqual(result["observation"]["error"]["code"], "invalid_submission")

    def test_submit_missing_findings_is_terminal(self):
        environment = self.environment()
        environment.reset()
        result = environment.step({"kind": "submit"})
        self.assertTrue(result["terminated"])
        self.assertEqual(result["observation"]["error"]["code"], "invalid_submission")

    def test_unsorted_ids_are_canonicalized_for_evaluator(self):
        evaluator = FixedEvaluator()
        environment = AuditEnvironment(episode_data(), evaluator=evaluator)
        environment.reset()
        result = environment.step({
            "kind": "submit", "findings": {"episode_id": "ep_test_001", "findings": [{
                "type": "duplicate_invoice", "affected_ids": ["invoice:I-2", "invoice:I-1"],
                "claimed_amount_minor": 100, "currency": "KZT",
                "evidence_ids": ["ledger:L-2", "ledger:L-1"], "explanation": "Order is irrelevant",
            }]},
        })
        self.assertTrue(result["terminated"])
        self.assertEqual(evaluator.findings["findings"][0]["affected_ids"], ["invoice:I-1", "invoice:I-2"])
        self.assertEqual(evaluator.findings["findings"][0]["evidence_ids"], ["ledger:L-1", "ledger:L-2"])

    def test_timeout_during_inspection_truncates_and_records_the_accepted_step(self):
        environment = AuditEnvironment(episode_data(), evaluator=FixedEvaluator(), clock=AdvancingClock())
        environment.reset()
        result = environment.step({"kind": "list_records", "table": "ledger", "cursor": None, "limit": 1})
        self.assertTrue(result["truncated"])
        self.assertEqual(result["info"]["step"], 1)
        self.assertEqual(environment.trajectory()[0]["observation"]["kind"], "truncated")

    def test_submit_rejects_noncanonical_or_unknown_currency_ids(self):
        environment = self.environment()
        environment.reset()
        result = environment.step(
            {
                "kind": "submit",
                "findings": {
                    "episode_id": "ep_test_001",
                    "findings": [
                        {
                            "type": "fx_date_or_rate",
                            "affected_ids": ["ledger:part:two"],
                            "claimed_amount_minor": 1,
                            "currency": "USD",
                            "evidence_ids": ["ledger:L-1"],
                            "explanation": "x",
                        }
                    ],
                },
            }
        )
        self.assertTrue(result["terminated"])
        self.assertEqual(result["observation"]["error"]["code"], "invalid_submission")

    def test_one_hundredth_non_submit_truncates_but_submit_has_priority(self):
        non_submit = self.environment()
        non_submit.reset()
        non_submit._step = 99
        truncated = non_submit.step({"kind": "list_records", "table": "ledger", "cursor": None, "limit": 1})
        self.assertTrue(truncated["truncated"])
        self.assertFalse(truncated["terminated"])
        self.assertIsNone(truncated["observation"]["result"])

        submit = self.environment()
        submit.reset()
        submit._step = 99
        terminal = submit.step({"kind": "submit", "findings": {"episode_id": "ep_test_001", "findings": []}})
        self.assertTrue(terminal["terminated"])
        self.assertFalse(terminal["truncated"])

    def test_trusted_runtime_uses_reset_seed_and_config_without_exposing_them(self):
        generator = Generator()
        runtime = EnvironmentRuntime(generator, evaluator_factory=lambda data, config: FixedEvaluator())
        reset = runtime.reset(
            42,
            {
                "dataset_profile": "train",
                "company_size": "small",
                "enabled_issue_types": ["duplicate_invoice"],
                "max_steps": 100,
                "time_limit_seconds": 60,
                "evaluation_mode": "open",
            },
        )
        self.assertEqual(generator.seed, 42)
        self.assertEqual(reset["info"]["episode_id"], "ep_test_001")
        self.assertNotIn("seed", reset["info"])
