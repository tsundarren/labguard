from scripts.accuracy import format_table, score_rules


def test_counts_true_positives_false_positives_and_misses():
    items = [
        {"message_id": "a", "errors": ["missing_field"]},   # injected, detected      -> TP
        {"message_id": "b", "errors": ["missing_field"]},   # injected, not detected  -> FN
        {"message_id": "c", "errors": []},                  # clean, flagged          -> FP
        {"message_id": "d", "errors": []},                  # clean, not flagged      -> TN
    ]
    found = {"a": {"missing_field"}, "c": {"missing_field"}}
    s = score_rules(items, found, ["missing_field"])["missing_field"]
    assert (s.tp, s.fn, s.fp) == (1, 1, 1)
    assert s.precision == 0.5 and s.recall == 0.5 and s.injected == 2


def test_rules_are_scored_independently():
    items = [{"message_id": "a", "errors": ["identity_mismatch"]}]
    found = {"a": {"identity_mismatch", "missing_field"}}  # extra rule fired on this message
    scores = score_rules(items, found, ["identity_mismatch", "missing_field"])
    assert scores["identity_mismatch"].tp == 1
    assert scores["missing_field"].fp == 1


def test_items_without_a_message_id_are_skipped():
    scores = score_rules([{"message_id": None, "errors": ["missing_field"]}], {}, ["missing_field"])
    assert scores["missing_field"].fn == 0


def test_precision_and_recall_are_none_when_undefined():
    s = score_rules([{"message_id": "a", "errors": []}], {}, ["missing_field"])["missing_field"]
    assert s.precision is None and s.recall is None


def test_table_formats_include_every_rule_and_a_total():
    scores = score_rules([{"message_id": "a", "errors": ["missing_field"]}], {"a": {"missing_field"}}, ["missing_field"])
    plain, md = format_table(scores), format_table(scores, markdown=True)
    assert "missing_field" in plain and "ALL (micro)" in plain and "100.0%" in plain
    assert md.startswith("| Rule |") and "---|" in md
